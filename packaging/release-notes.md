Prebuilt executables of the Forensic Video Processor {{VERSION}}, one archive per platform. Each holds the graphical front end, the command line tool, a README and SHA256 checksums. Python, PyAV and the FFmpeg libraries are bundled; nothing needs to be installed.

| Archive | Platform |
|---|---|
| `*-windows-x64.zip` | Windows 10/11, 64-bit Intel or AMD |
| `*-windows-arm64.zip` | Windows 11 on ARM |
| `*-macos-arm64.zip` | macOS on Apple Silicon |
| `*-macos-x64.zip` | macOS on Intel |
| `*-linux-x64.tar.gz` | Linux, x86-64, glibc 2.35 or newer |
| `*-linux-arm64.tar.gz` | Linux, ARM64, glibc 2.35 or newer |

Every archive was built by the `Build executables` workflow on a GitHub-hosted runner of that platform, and on that runner the command line executable was run against the damaged videos in `test_videos/corrupt` and checked for the expected frame counts and rejected packets before packaging. `SHA256SUMS.txt` next to the archives lists the checksum of each archive.

**Caveats**

- The executables are not code-signed or notarised. Windows SmartScreen will warn on first launch (choose More info, then Run anyway). macOS Gatekeeper refuses the app until you right-click it and choose Open, or remove the quarantine attribute with `xattr -dr com.apple.quarantine`. The README inside each archive has the exact steps.
- Single-file executables unpack to a temporary folder on every start, so the first launch can take 10 to 20 seconds.
- The Linux builds need an X11 or XWayland display for the graphical front end.
- Full forensic mode writes one PNG per frame, roughly 1 to 2 MB each at 1080p. Use the estimate before processing long material.
- The `native_sha256` frame hash is comparable between platforms and with `ffmpeg -f framehash`. The `decoded_sha256` (rgb24) hash reproduces on the same platform only; see the README for why.
