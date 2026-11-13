# Third-party software: the window

The window (`video_processor_gui.py`) uses these packages when they are installed. They are
bundled into the prebuilt `video_processor_gui` executables. Without CustomTkinter the plain Tk
window opens instead, and runs the same code.

| Package, version read | Used for | Licence, as read from the wheel |
|---|---|---|
| CustomTkinter 6.0.0 (Tom Schimansky) | the window's look | MIT (the LICENSE file in the wheel). The wheel's `License:` metadata line says "Creative Commons Zero v1.0 Universal" while its classifier says MIT; the LICENSE file is MIT. |
| darkdetect 0.8.0 | installed with CustomTkinter, reads the system's light or dark setting | BSD-3-Clause |
| packaging 26.3 | installed with CustomTkinter | Apache-2.0 OR BSD-2-Clause |

Their licence texts, copied from the wheels, are in `licenses/` and go into each prebuilt archive.

The window's logo (`assets/`) was drawn for this repository and is under its MIT licence.

This file covers what the window added. The packages the processing itself uses (PyAV and the
FFmpeg libraries it carries, NumPy, Pillow) are listed in `requirements.txt`; their licences are
not collected here.
