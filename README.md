# frame-counts-galore
Python wrapper for FFMPEG that extracts frames from media.

Dependencies for your python environment are listed in requirements.txt

Install them using the below command. Ensure the py part is correct for your environment, eg py, python, or python3, etc.

py -m pip install -r requirements.txt\
or\
pip3 install -r requirements.txt

FFMPEG needs to be installed on your system.

Script processes video files to extract the following:
* All frames in the video
* Calculates the frames per second to include average variable frame rates
* Calculates the lenght of the video

Script also provides a CSV file per video with the following columns:
* Frame index
* Presentation Timestamp (PTS)
* Time base
* Timestamp
* Frame duration
* Instant FPS
* Key Frame
* Decoded SHA 256 for pixel data
* Image SHA 256 for pixel data after frame extraction is written to disk
* Hash verified field
* Image filename
* Decoding method (Always CPU)

Script generates a case manifest in json and a case log for all recorded script events.

usage: video_processor_cli.py [-h] -i INPUT -o OUTPUT

Forensic Video Processor CLI

options:\
  -h, --help            &emsp;show this help message and exit\
  -i INPUT, --input INPUT\
                        &emsp;&emsp;&emsp;&emsp;Input directory or video file\
  -o OUTPUT, --output OUTPUT\
                       &emsp;&emsp;&emsp;&emsp; Output directory for case

Sample screen output
<img width="1517" height="400" alt="Screenshot 2025-12-23 at 7 12 57 PM" src="https://github.com/user-attachments/assets/71730bf7-f7e6-4a69-85aa-d0be40b82b96" />

Sample output folder structure for 3 processed files\
<img width="464" height="171" alt="Screenshot 2025-12-23 at 9 09 53 PM" src="https://github.com/user-attachments/assets/f6793348-b65a-477d-85f6-64f7294bd6b1" />

Sample spreadsheet
<img width="1363" height="385" alt="Screenshot 2025-12-23 at 9 12 00 PM" src="https://github.com/user-attachments/assets/2a6bdf70-9758-4288-8e3e-ef88e65779cc" />

Sample frames directory with files named by index and pts\
<img width="326" height="259" alt="Screenshot 2025-12-23 at 9 12 46 PM" src="https://github.com/user-attachments/assets/25f2778e-faec-49fd-9c1d-bd72fd7ae157" />
