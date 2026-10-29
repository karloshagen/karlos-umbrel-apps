# Karlos Apps

Community app store for umbrelOS. The store id is `karlos`.

## Add this store

1. Open the App Store from the dock.
2. Click the three dots in the top-right corner and select **Community App Stores**.
3. Paste this URL and click **Add**:

   `https://github.com/karloshagen/karlos-umbrel-apps`

4. Open **Karlos Apps** from that same menu and install apps the same way as from the official store.

Community stores are not reviewed by Umbrel. Add this one only if you trust this repository.

Removing the store does not uninstall apps that came from it.

## Immich (NAS)

`karlos-immich` is Immich v3.3.5, packaged separately from the official Umbrel Immich app so both can be installed.

| | Official Immich | Immich (NAS) |
| --- | --- | --- |
| App id | `immich` | `karlos-immich` |
| Browser port | 2283 | **2284** |
| Containers | `immich_*` | `karlos-immich_*` |
| Originals | This app's data directory | NAS folder below |

Before installing, mount the **Personal-Drive** share in Umbrel Files. The share host is `UNAS-Pro-4.local`. The app expects that mount at:

`/home/umbrel/umbrel/network/UNAS-Pro-4.local/Personal-Drive`

On first start the pre-start hook creates `Photos/Immich` on that share when the share is mounted and the folder is missing. The compose file sets `create_host_path: false` on that bind, so a down share stays unmounted instead of being covered by an empty local directory.

### What is stored where

| Data | Where |
| --- | --- |
| Originals (`upload/`, `library/`), profile pictures, backups | `/home/umbrel/umbrel/network/UNAS-Pro-4.local/Personal-Drive/Photos/Immich` |
| Thumbnails (`/data/thumbs`) | Local app data (`${APP_DATA_ROOT}/thumbs`) |
| Transcoded video (`/data/encoded-video`) | Local app data (`${APP_DATA_ROOT}/encoded-video`) |
| Postgres, Redis, ML model cache | Local app data |

Immich's own guide mounts a NAS at `/data` and then bind-mounts `/data/thumbs` and `/data/encoded-video` from local disk. `upload/` and `library/` stay on the same NAS mount; Immich advises against splitting those two. The server container still listens on port 2283. umbrelOS publishes the app on port 2284.

GPU access is the same manifest permission as the official app (`permissions: [GPU]`). umbrelOS injects `/dev/dri` (and any other acceleration it finds) into the containers. Image digests are pinned to Immich v3.3.5.

Postgres uses the same local-only password as the official Umbrel Immich compose file. It is not a new credential and it is not exposed outside the app network. The UI login is the admin account created in Immich's first-run setup. `JWT_SECRET` comes from the per-app Umbrel seed.

### Caveats

- This is not an upgrade of the official Immich app. It has its own database. Do not point it at the official app's upload folder, and do not point the official app at `Photos/Immich`.
- Do not pick `Photos/Immich` as the read-only external library. That folder is already the upload library. Point the external library at some other folder if you want Immich to index files in place.
- Immich's storage bar follows `/data`, which is the NAS, not the local disk that holds thumbnails and transcodes. A full Umbrel disk will not show up there. Those two local directories are omitted from Umbrel backups because Immich can rebuild them; the database is not omitted.
- The share is the CIFS mount Umbrel maintains (`uid`/`gid` 1000, `file_mode=0660`, `dir_mode=0770`). Immich starts only after it can create, read, and overwrite a `.immich` file in each storage folder. On Samba those dotfiles are DOS-hidden, and the overwrite (`open` with read/write, the Node `r+` flag) has been seen to fail with `EACCES` after the file was created. The pre-start hook performs that same sequence as root and as uid 1000, and if the overwrite fails it clears the hidden and read-only DOS attributes via the CIFS xattrs before retrying. The startup mount check stays enabled.
- If the server still logs `Failed to write .../.immich`, the share is denying that open. Fix the mount before putting photos on it. `IMMICH_IGNORE_MOUNT_CHECK_ERRORS=true` skips the check and will hide a library that is not actually writable.
- The server image runs as root, matching the official Umbrel package, so `/dev/dri` stays usable. Files on this CIFS mount still appear as uid/gid 1000 because that is how Umbrel mounts the share.
- Keep another copy of your photos. Immich is not a backup by itself.
