from __future__ import annotations

import base64
import json
import os
import queue
import subprocess
import threading
import webbrowser
from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk
import requests
from tkinter import messagebox

# --------------------------------------------------------------------------
# Configuration storage (equivalent of electron-store)
# --------------------------------------------------------------------------

CONFIG_DIR = Path.home() / ".ghsync-gui"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "storagePath": "",
    "pat": "",
    "ignoredRepos": [],
    "lfs": False,
}


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_CONFIG)
    config = dict(DEFAULT_CONFIG)
    config.update(data)
    return config


def save_config(config: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    masked = dict(config)
    if masked.get("pat"):
        masked["pat"] = "*" * len(masked["pat"])
    print("Saved configuration:", json.dumps(masked))


# --------------------------------------------------------------------------
# Git helpers
# --------------------------------------------------------------------------

def run_git_command(args: list[str], cwd: str, pat: Optional[str] = None) -> str:
    """Run a git command, optionally authenticated with a PAT. Returns stdout."""
    os.makedirs(cwd, exist_ok=True)

    cmd = ["git"]
    if pat:
        basic_auth = base64.b64encode(f"x-access-token:{pat}".encode()).decode()
        cmd += ["-c", f"http.extraHeader=Authorization: Basic {basic_auth}"]
    cmd += args

    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)

    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip()
            or f"Git command failed with exit code {result.returncode}"
        )

    return result.stdout.strip()


# --------------------------------------------------------------------------
# GitHub repo helpers
# --------------------------------------------------------------------------

class Repo(dict):
    """Keys: name, full_name, clone_url, path."""


def get_all_repos(pat: str, storage_path: str) -> list[Repo]:
    response = requests.get(
        "https://api.github.com/user/repos",
        params={"per_page": 100},
        headers={
            "Authorization": f"token {pat}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30,
    )

    if not response.ok:
        if response.status_code == 401:
            raise RuntimeError(
                "Invalid Personal Access Token (PAT). Please ensure it is "
                "correct, not expired, and has the necessary permissions."
            )
        raise RuntimeError(f"{response.status_code} {response.reason}")

    repos_json = response.json()
    repos: list[Repo] = []
    for repo in repos_json:
        owner, name = repo["full_name"].split("/")
        repos.append(
            Repo(
                name=repo["name"],
                full_name=repo["full_name"],
                clone_url=repo["clone_url"],
                path=str(Path(storage_path) / owner / f"{name}.git"),
            )
        )
    return repos


def clone_repos(pat, all_repos, ignored_repos, progress_cb):
    cloned = 0
    ignored = 0
    failed_to_clone: list[Repo] = []

    repos_to_clone = []
    for repo in all_repos:
        if os.path.exists(repo["path"]):
            continue
        if repo["full_name"] in ignored_repos:
            ignored += 1
            continue
        repos_to_clone.append(repo)

    total = len(repos_to_clone)
    for index, repo in enumerate(repos_to_clone):
        progress_cb(f"Cloning {index + 1}/{total}: {repo['full_name']}")
        try:
            run_git_command(
                ["clone", repo["clone_url"], repo["path"], "--mirror"],
                repo["path"],
                pat,
            )
        except Exception:
            failed_to_clone.append(repo)
        finally:
            cloned += 1

    return cloned, ignored, failed_to_clone


def update_repos(storage_path, pat, lfs, progress_cb):
    absolute_base = Path(storage_path).resolve()
    repo_abs_paths = []

    if absolute_base.exists():
        for parent in sorted(absolute_base.iterdir()):
            if not parent.is_dir():
                continue
            for child in sorted(parent.iterdir()):
                if child.is_dir():
                    repo_abs_paths.append(child)

    updated = 0
    failed_to_update: list[str] = []
    total = len(repo_abs_paths)

    for index, repo_abs_path in enumerate(repo_abs_paths):
        repo_full_name = "/".join(repo_abs_path.parts[-2:])
        if repo_full_name.endswith(".git"):
            repo_full_name = repo_full_name[: -len(".git")]

        progress_cb(
            f"Updating {'and fetching LFS files ' if lfs else ''}"
            f"{index + 1}/{total}: {repo_full_name}"
        )

        try:
            run_git_command(["fetch"], str(repo_abs_path), pat)
            if lfs:
                run_git_command(["lfs", "fetch", "--all"], str(repo_abs_path), pat)
            updated += 1
        except Exception:
            failed_to_update.append(repo_full_name)

    return updated, failed_to_update


# --------------------------------------------------------------------------
# Sync orchestration (runs on a background thread)
# --------------------------------------------------------------------------

def run_sync(
    config: dict,
    progress_cb: Callable[[str], None],
    error_cb: Callable[[str, str], None],
) -> None:
    storage_path = config.get("storagePath", "")
    pat = config.get("pat", "")
    ignored_repos = config.get("ignoredRepos", [])
    lfs = config.get("lfs", False)

    progress_cb("")

    if not storage_path:
        error_cb("Configuration Error", "Path to the backup folder is not configured.")
        return
    if not pat:
        error_cb("Configuration Error", "Personal Access Token (PAT) is not configured.")
        return

    try:
        run_git_command(["version"], os.getcwd())
    except Exception:
        error_cb("Git not installed", "Please ensure git is installed and added to PATH.")
        return

    if lfs:
        try:
            run_git_command(["lfs", "version"], os.getcwd())
        except Exception:
            error_cb("LFS not installed", "Please install LFS and try again.")
            return
        try:
            run_git_command(["lfs", "install"], os.getcwd())
        except Exception as e:
            error_cb(
                "Failed to initialize LFS",
                "We tried to run `git lfs install` but failed with the "
                f"following error message: {e}",
            )
            return

    try:
        all_repos = get_all_repos(pat, storage_path)
    except requests.exceptions.ConnectionError:
        error_cb("Failed to fetch repos", "Please check your internet connection and try again.")
        return
    except Exception as e:
        error_cb("Failed to fetch repos", str(e))
        return

    try:
        cloned, ignored, failed_to_clone = clone_repos(pat, all_repos, ignored_repos, progress_cb)
    except Exception as e:
        error_cb("Failed to clone repos", str(e))
        return

    updated, failed_to_update = update_repos(storage_path, pat, lfs, progress_cb)

    if failed_to_clone:
        names = " ".join(r["full_name"] for r in failed_to_clone)
        error_cb(
            f"Failed to clone {'' if cloned == 0 else 'some '}repositories",
            "The following repositories could not be cloned:\n\n"
            f"{names}\n\n"
            "Please check your internet connection or PAT (make sure it is "
            "not expired) and try again.",
        )

    if failed_to_update:
        names = " ".join(failed_to_update)
        error_cb(
            f"Failed to update {'' if updated == 0 else 'some '}repositories",
            "The following repositories could not be updated:\n\n"
            f"{names}\n\n"
            "Please check your internet connection or PAT (make sure it is "
            "not expired) and try again. If the issue persists, consider "
            "moving the above repositories outside the backup folder to "
            "trigger a re-clone (this could fix many issues due to "
            "corrupted local repositories).",
        )

    has_errors = bool(failed_to_clone or failed_to_update)
    summary = ", ".join(
        [
            f"{cloned} cloned",
            f"{ignored} ignored",
            f"{len(failed_to_clone)} failed to clone",
            f"{updated} updated",
            f"{len(failed_to_update)} failed to update",
        ]
    )
    progress_cb(f"Finished{' with ERRORS' if has_errors else ''}. Summary: {summary}.")


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------

ctk.set_appearance_mode("system")
ctk.set_default_color_theme("blue")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("ghsync-gui")
        self.geometry("560x580")
        self.minsize(420, 500)

        self._progress_queue: "queue.Queue[tuple]" = queue.Queue()

        self._build_ui()
        self._load_config_into_form()
        self.after(100, self._poll_queue)

    # -- UI construction ---------------------------------------------------

    def _build_ui(self):
        pad = {"padx": 20, "pady": (6, 0)}

        header = ctk.CTkLabel(self, text="Configuration", font=ctk.CTkFont(size=20, weight="bold"))
        header.pack(anchor="w", **pad)

        ctk.CTkLabel(self, text="Folder to backup your repositories to").pack(anchor="w", **pad)
        self.storage_path_entry = ctk.CTkEntry(
            self, placeholder_text=r"e.g., C:\Users\username\backups"
        )
        self.storage_path_entry.pack(fill="x", padx=20, pady=(2, 0))

        pat_label_frame = ctk.CTkFrame(self, fg_color="transparent")
        pat_label_frame.pack(fill="x", **pad)
        ctk.CTkLabel(pat_label_frame, text="Your GitHub PAT (classic)").pack(side="left")
        link = ctk.CTkLabel(
            pat_label_frame, text="Learn more", text_color=("blue", "#4ea1ff"), cursor="hand2"
        )
        link.pack(side="left", padx=(6, 0))
        link.bind(
            "<Button-1>",
            lambda e: webbrowser.open(
                "https://github.com/effessdev/ghsync-gui#pat-configuration"
            ),
        )

        self.pat_entry = ctk.CTkEntry(self, placeholder_text="e.g., ghp_...", show="*")
        self.pat_entry.pack(fill="x", padx=20, pady=(2, 0))

        ctk.CTkLabel(self, text="Ignored repositories (full name, separated by spaces)").pack(
            anchor="w", **pad
        )
        self.ignored_repos_entry = ctk.CTkEntry(self, placeholder_text="e.g., torvalds/linux")
        self.ignored_repos_entry.pack(fill="x", padx=20, pady=(2, 0))

        self.lfs_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            self,
            text="Enable Git LFS (leave it off if you are unsure)",
            variable=self.lfs_var,
        ).pack(anchor="w", padx=20, pady=(10, 0))

        self.save_config_btn = ctk.CTkButton(
            self, text="Save Configuration", command=self._on_save_config
        )
        self.save_config_btn.pack(fill="x", padx=20, pady=(14, 0))

        sep = ctk.CTkFrame(self, height=2, fg_color=("gray80", "gray25"))
        sep.pack(fill="x", padx=20, pady=16)

        ctk.CTkLabel(
            self,
            text=(
                "Before syncing, ensure that Git (and Git LFS if it is turned on)\n"
                "is installed in your system and is connected to the internet."
            ),
            justify="left",
        ).pack(anchor="w", padx=20)

        self.sync_btn = ctk.CTkButton(self, text="Sync Now!", command=self._on_sync)
        self.sync_btn.pack(fill="x", padx=20, pady=(12, 8))

        self.progress_label = ctk.CTkLabel(
            self, text="", justify="left", anchor="w", wraplength=500
        )
        self.progress_label.pack(fill="x", padx=20, pady=(0, 16))

    # -- Config persistence --------------------------------------------------

    def _load_config_into_form(self):
        config = load_config()
        self.storage_path_entry.delete(0, "end")
        self.storage_path_entry.insert(0, config.get("storagePath", ""))

        self.pat_entry.delete(0, "end")
        self.pat_entry.insert(0, config.get("pat", ""))

        self.ignored_repos_entry.delete(0, "end")
        self.ignored_repos_entry.insert(0, " ".join(config.get("ignoredRepos", [])))

        self.lfs_var.set(bool(config.get("lfs", False)))

    def _collect_config_from_form(self) -> dict:
        return {
            "storagePath": self.storage_path_entry.get(),
            "pat": self.pat_entry.get(),
            "ignoredRepos": self.ignored_repos_entry.get().split(),
            "lfs": bool(self.lfs_var.get()),
        }

    def _on_save_config(self):
        save_config(self._collect_config_from_form())
        original = self.save_config_btn.cget("text")
        self.save_config_btn.configure(text="Saved")
        self.after(2000, lambda: self.save_config_btn.configure(text=original))

    # -- Sync ------------------------------------------------------------

    def _on_sync(self):
        self.sync_btn.configure(state="disabled", text="Syncing (do not close the app)...")
        self.save_config_btn.configure(state="disabled")

        config = load_config()

        def progress_cb(message: str):
            self._progress_queue.put(("progress", message))

        def error_cb(title: str, message: str):
            self._progress_queue.put(("error", title, message))

        def worker():
            try:
                run_sync(config, progress_cb, error_cb)
            finally:
                self._progress_queue.put(("done", None))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_queue(self):
        try:
            while True:
                item = self._progress_queue.get_nowait()
                kind = item[0]
                if kind == "progress":
                    self.progress_label.configure(text=item[1])
                elif kind == "error":
                    messagebox.showerror(item[1], item[2])
                elif kind == "done":
                    self.sync_btn.configure(state="normal", text="Sync Again")
                    self.save_config_btn.configure(state="normal")
        except queue.Empty:
            pass
        finally:
            self.after(100, self._poll_queue)


if __name__ == "__main__":
    app = App()
    app.mainloop()
