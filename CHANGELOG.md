# Changelog

Kodi shows the `<news>` element from `addon.xml` in the add-on's information dialog, not this file. This is the fuller history, with the commits behind each release: `dev/scripts/bump-version.sh` adds a new section from the git log, and the prose under each heading is written by hand.

## p0.9.9-pre (2026-08-15)

A test build for FCast protocol **v3**. Not published to the add-on repository: install the zip by hand if you want to try it.

**Protocol v3.** The receiver now announces v3 and implements it: the `Initial` handshake, so a sender that connects mid-playback is told what is already on screen; `PlayUpdate`, so several senders stay in step with each other; event subscription with `MediaItemEnd`, which is what lets a sender's own queue move to the next item; `SetPlaylistItem`; and volume in both directions.

**Grayjay queues.** A video that played to its end left Grayjay's queue stranded. The receiver now reports the item that finished, with the item attached — a report with nothing in it tells the sender nothing, so its queue never advanced.

**Pictures.** Cast photos used to be handed to the video player, which showed them for a few milliseconds and closed. They now go to Kodi's picture viewer:

- Photos are downloaded before being shown, so the picture already up stays there instead of the screen going black for the length of the download. The last dozen are kept, so stepping back through a slideshow costs nothing
- A picture on screen counts as the box being in use. Kodi treats a single picture as an idle screen, so its screensaver would start up behind the picture and, on some skins, be audible when it could not come to the front
- A sender re-sending the picture already up no longer tears the viewer down and rebuilds it for the same photo
- Stop from a sender closes the viewer, and closing it from Kodi tells senders

**Playlists.** A sender can hand over a whole queue, and the receiver walks it: offset, per-item volume and speed, jumping between items, and `showDuration` for pictures, so a photo in a queue moves on by itself.

**Settings**, at Add-ons → Services → FCast Receiver → Configure: on-screen notifications, picture downloading, keeping the screen awake, and how long a picture in a playlist stays up.

**Failures are visible.** Everything the add-on logged was `LOGDEBUG`, which Kodi hides unless debug logging is on, so a failed download, a viewer that never opened, or discovery that never registered all looked exactly like working. Those now log at warning level, and connections at info.

- (b64516) Tests for image viewer updated
- (6393f2) README updated
- (1c8086) Keep awake when picture is shown added as a feature
- (7dc955) prerelase workflow added, release workflow clarified
- (7ec9ea) mdns tests added, image viever tests expanded
- (5a27d6) Enabled logging
- (4870f8) Enabled logging
- (03d388) Tests image viewer
- (1bfaa3) Minor fix in player image viewer operation
- (e4053e) Tests for image view
- (ba178a) README updated
- (253c53) Minor improvements on image caching and viewing
- (e18b83) Makefile updated
- (289a47) README updated
- (324be6) Image caching tests
- (ae4bf3) Image caching for better image handling
- (a46a79) README updated
- (2ded75) Tests for settings
- (cdce84) Settings added
- (383ecc) Language file added for settings
- (a4c192) Add an FCast sender for testing the receiver
- (0bb613) playlist tests
- (252cd7) playlist functions added
- (e9d0e3) image viewer tests
- (9d775b) image viewer stop fix when video is playing take 2
- (1da16f) image viewer tests
- (c3c214) image viewer stop fix when video is playing reverted
- (112042) image viewer tests
- (10a329) image viewer stop fix when video is playing
- (3652a5) image viewer tests
- (1ac3e9) image viewer stop fix
- (426283) tests for image view
- (f51eda) minor fixes on image view
- (b06d15) gitignore updated
- (6fcee6) Tests for image viewer
- (0e2fe2) image viewer
- (4e8c52) Volume tests added
- (801d6c) Volume controls added
- (019103) Test suite update
- (15dcd9) onPlayBackEnded sequence fixed
- (3c1633) Test suite update
- (18ed76) Fixes on media item end handling and sender receiver comms
- (dbddf4) Fixes on sender receiver communications
- (7490a5) Fixes on sender receiver communications
- (23fd28) proxy for debugging added
- (4f9c5d) tracer update
- (5a3243) tracer for debugging added
- (771f6d) test suite changes
- (20acfb) major change on stop and error handling, now idles instead of a hard stop by xbmc
- (48cf70) protocol v3 changes and bug fixes
- (9e616d) protocol v3 changes and bug fixes
- (4f3d3a) protocol v3 classes added

## v0.2.2-beta (2026-08-15)

- Fixed the add-on interrupting playback it did not start. Kodi delivers playback callbacks for everything it plays, and the add-on answered each one by issuing a stop. With gapless audio the callback for a finished track arrives while the next one is already playing, so the stop killed the track that had just started, cutting an album to a few seconds a song
- The add-on now only acts on playback it started itself. Casting is unchanged; local music and video are left alone entirely, including the playback position updates that were being sent to connected senders for media nobody had cast

## v0.2.1-beta (2026-08-15)

- Fixed a crash whenever Kodi played media the add-on did not start. The add-on receives playback callbacks for everything Kodi plays, not only what was cast to it, and raised AttributeError on every one of them
- Fixed a second crash in the same path: Kodi raises "Kodi is not playing any media file" from getTime() once the player has moved on, which happens routinely with gapless audio where the playback-started callback arrives after playback has already advanced
- Both were reported from the field while playing local music, and neither affected casting itself, but each logged an error and skipped a playback update

## v0.2.0-beta (2026-08-13)

- Fixed packet reassembly: FCast messages split across TCP reads were corrupted, which desynchronised the stream and dropped the connection. Larger Play messages hit this almost every time, which is why Grayjay and other newer senders appeared to stop working
- Unknown opcodes, unimplemented opcodes and unrecognised message fields are now ignored instead of closing the connection, so senders on protocol v3 are no longer disconnected mid-handshake
- Protocol version is now negotiated properly, and the receiver no longer echoes a second Version message back at the sender
- HTTP headers sent with a Play request are now applied, as inputstream.adaptive manifest and stream headers or appended to the URL for direct playback. CDNs that check Referer or User-Agent rejected these streams before
- HLS and DASH are now detected from the container the sender declares as well as from the URL extension
- mDNS discovery now works on LibreELEC and CoreELEC (DBussy) as well as Debian and Raspbian (python-dbus), with an avahi-publish fallback. A missing binding no longer stops the add-on from starting
- mDNS now advertises TXT records (version, appName, appVersion); some senders misbehave against a service that publishes none
- Disconnected clients no longer leave a thread spinning for the lifetime of the Kodi session
- Start-up failures are now reported on screen and logged with a full traceback, instead of leaving the service silently dead
- Add-on can now be installed from a repository, so Kodi keeps it up to date automatically

## v0.1.1-beta (2026-04-04)

- mDNS broadcasting: receiver is now discoverable by sender devices on the local network
- Speed control: playback speed clamped to Kodi's supported range (0.8x–1.5x)
- CastLab compatibility fixes (beta support for Android CastLab app)
- Playback position sync: streams now start from the sender's current playback position
- Fixed stream cancellation freeze: stopping or switching streams mid-play no longer freezes Kodi
- Fixed shared session listeners bug that could cause duplicate event handling with multiple connections
- Recommended setting: enable Settings → Player → Videos → Sync playback to display for compatibility
