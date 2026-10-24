<h1 align="center">ghsync-gui</h1>

<p align="center">Back up GitHub repositories with a single click. Supports both light and dark modes.<p>

<p align="center">
  <img width="480" alt="Preview" src="https://github.com/user-attachments/assets/ed5a0e1e-28ff-495a-90d7-4798cc9d3668" />
</p>

## How to use

1. Paste the path to your backup folder
2. Paste your PAT (classic, not fine-grained; must have at least _repo_ scope) [Learn more](#pat-configuration)
3. Click Sync

## PAT configuration

1. Go to [github.com](https://github.com)
2. Click your profile picture and click Settings
3. Scroll down the left sidebar and click Developer Settings (usually at the very end)
4. Open the _Personal access tokens_ dropdown and click _Tokens (classic)_
5. Click _Generate new token_ and select _classic_ (**not** fine-grained)
6. Add a note (e.g., `ghsync-gui-pat`)
7. Select an expiration date (or use the default)
8. Select scope _repo_ (this should automatically select everything underneath)
9. Scroll down and click _Generate token_
10. Copy the generated token (you won't be able to see it again, it starts with `ghp_`)

After that, paste the token into the PAT field in the app. The token is stored locally and is never transmitted elsewhere except the GitHub API.

> [!IMPORTANT]
> You should use a classic GitHub PAT instead of a fine-grained PAT, otherwise `git lfs fetch` would fail. We currently don't know the exact reason for this failure. If you keep LFS turned off, you can _probably_ use a fine-grained PAT.

## How it works

When you click Sync, the GitHub repositories you have access to (includes your own repos, repos of your organizations, and others) are retrieved from the GitHub API using your Personal Access Token (PAT).

Each repository is then cloned as a mirror using `git clone --mirror` into the configured directory, but only if it has not already been cloned locally. After that, each repository is updated using `git fetch` (and `git lfs fetch --all` if LFS is enabled), keeping the local mirror in sync with GitHub.

> [!NOTE]
> These clones are bare mirrors intended for backup purposes and do not include a working directory.

## Why this exists

Data loss may occur due to

- **Accidental force pushes:** Rewritten branches can make older states practically undiscoverable.
- **Accidental repo deletion:** GitHub can only recover repos if they were deleted within 90 days.
- **Account lockout:** Suspension, credential loss, or SSO misconfiguration can block access across all repositories at once.

## How to use

### Step 1: Download source code

Download [ghsync_gui.py](ghsync_gui.py) file from this repo.

## Step 2: Install dependencies.

After installing Python, run the following command:

```
pip install customtkinter requests
```

### Step 3: Run the app

Run `ghsync_gui.py` to use the app.
