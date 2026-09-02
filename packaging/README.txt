Forensic Video Processor, prebuilt executables
Source: https://github.com/abrignoni/frame-counts-galore

Contents
  video_processor_gui        Graphical front end (video_processor_gui.exe on Windows,
                             video_processor_gui.app on macOS). Pick a video file or
                             folder and an output folder, press Estimate to see the
                             expected frame count, run time and disk use, then Start.
  video_processor_cli        Command line tool (video_processor_cli.exe on Windows).
                             video_processor_cli -i video.mp4 -o output --estimate
                             video_processor_cli -i video.mp4 --estimate-only
  SHA256SUMS.txt             Checksums of the files above.

Each executable is a single file with Python, PyAV and the FFmpeg libraries bundled.
No installation is needed and no system FFmpeg is used. The first start takes a few
seconds while the file unpacks to a temporary folder.

The executables are NOT code-signed or notarised.
  Windows: SmartScreen warns on first launch. Choose "More info", then "Run anyway".
  macOS:   Gatekeeper refuses an app downloaded from the internet that is not
           notarised. Either right-click (Control-click) the app and choose Open, or
           remove the quarantine attribute in Terminal:
             xattr -dr com.apple.quarantine video_processor_gui.app video_processor_cli
           The Apple Silicon build carries an ad-hoc signature so it can run at all;
           the Intel build runs on Apple Silicon through Rosetta but is slower.
  Linux:   Built on Ubuntu 22.04, so it needs glibc 2.35 or newer, and the graphical
           front end needs an X11 or XWayland display. Mark the files executable if
           the archive lost the permission bit: chmod +x video_processor_*

Verify the download against SHA256SUMS.txt before use:
  Windows:  certutil -hashfile video_processor_gui.exe SHA256
  macOS:    shasum -a 256 -c SHA256SUMS.txt
  Linux:    sha256sum -c SHA256SUMS.txt

Full forensic mode writes one PNG per frame (roughly 1 to 2 MB each at 1080p). Run the
estimate before processing long material.
