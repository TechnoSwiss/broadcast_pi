# YouTube Live (RTMP) Automated Broadcast from Raspberry Pi

We setup this project to broadcast Sunday services for several wards (congregations) in my local area. However these instructions should work for anybody looking to broadcast to YouTube (or any source that accepts an rtmp stream) using a Raspberry Pi.

First up is a list of hardware that was used:

(Llinks are not affiliate links and just provided if you want to know the exact hardware I ordered)
1) Rasberry Pi 4 Model B 4GB (my setup runs a load average around 2, with each core running about 70%, and 500MB of memory usage, so you can probably use the 1GB or 2GB model 4's, but I don't know that it would work well with a 3) https://www.pishop.us/product/raspberry-pi-4-model-b-4gb/
2) Armor case with Fans (I found that a standard Armor case without fans wasn’t able to keep the Raspberry Pi cool enough on its own, and I was hitting the temp. throttle) https://www.pishop.us/product/armor-case-with-dual-fan-for-raspberry-pi-4-model-b-black/
3) Samsung 32GB EVO Plus Class 10 SDHC card https://www.amazon.com/gp/product/B00WR4IJBE
4) Labists Raspberry Pi 4 Power Supply (any USB-C power supply with sufficient current rating should work, I’ve just used this one in the past and knew it was a solid supply) https://www.amazon.com/gp/product/B07WC2HLJ9
5) Amcreset 1080P Webcam (AWC205) (my preference here would have been for a Logitech C920, but those have gotten really hard to come by, the important thing here is you need a camera that will OUTPUT h264 which is what YouTube requires for the upload, the Pi won't have enough processing power to encode a non-h264 stream with a framerate any better than about 2fps) https://www.amazon.com/gp/product/B088TT8HVY
6) V-Top USB Audio Grabber for Cassette Tapes to MP3 (you'll need something for audio input as the mic on the camera isn't great, for my setup I'm grabbing the audio from the chapel's sound system direct, so I needed something that could take a line-in signal) https://www.amazon.com/gp/product/B019T9KS04
7) Tripod to mount the camera, various interface cables (power, ethernet, 3.5mm extension cable, etc.)

The first step in the process is to get the latest version of Raspberry Pi OS Lite installed on the system. For that follow the latest instructions from raspberrypi.org. Make sure to use the Lite or Desktop Free version. (there might be enough processing power to run the desktop along with this, and it could makes some things easier to have a local desktop, but I’ve been running without so I’m not burning processing power on a desktop since these are all setup headless for my use)

Since we’ll be using this for streaming, I recommend running wired network to the RPi, but that’s not always the case. If you need to run wireless follow the directions for setting up the wifi network on the RPi using wpa_supplicant.conf. This is a headless setup, so make sure to create a text file named “ssh” on the RPi’s boot partition so when you start it up you’ll have access to it.

Get the RPi powered up and on your network, then SSH into it using your favorite SSH program (I generally use PuTTY). Make sure you change the password from the default to secure your RPi, and it probably wouldn’t hurt to change the username as well. Make sure your RPi is up to date (sudo apt update; sudo apt full-upgrade;) and you might want to go ahead and install your favorite text editor at this point as well.

Next step is to get ffmpeg installed, and for that I compiled from source instead of using a pre-packaged binary. It does take some time to compile from source, so in my case I did it once and then cloned the SD card to each new system that I setup. I followed the instructions at https://iotfuse.com/2020/03/25/setting-up-a-raspberry-pi/ to compile, but here’s a quick rundown:
sudo apt install git libasound2-dev libmp3lame-dev
git clone --depth 1  https://code.videolan.org/videolan/x264.git
cd x264
./configure --host=arm-unknown-linux-gnueabi --enable-static --disable-opencl
make -j4
sudo make install
cd ..
git clone git://source.ffmpeg.org/ffmpeg --depth=1
cd ffmpeg
 ./configure --arch=armel --target-os=linux --enable-gpl --enable-libx264 --enable-nonfree --enable-libmp3lame --enable-omx --enable-omx-rpi --extra-ldflags="-latomic"
make -j4
sudo make install

Now that ffmpeg is installed, make sure everything is plugged in and lets verify that the pi can see everything.

v4l2-ctl --list-devices

This should give a list of video devices, it should look something like:

HD264 Webcam USB: HD264 Webcam  (usb-0000:01:00.0-1.2):
        /dev/video0
        /dev/video1
        /dev/video2
        /dev/video3

Then you’ll need to check the supported formats and resolutions.

ffmpeg -f video4linux2 -list_formats all -i /dev/video2

You might need to check the formats and resolutions for each of the video devices listed to find the h264 format that you need. The Logitech cameras show an h264 format available on /dev/video0, while the Amcrest camera it was on /dev/video2 like:

  built with gcc 8 (Raspbian 8.3.0-6+rpi1)
  configuration: --arch=armel --target-os=linux --enable-gpl --enable-libx264 --enable-nonfree --enable-libmp3lame --enable-omx --enable-omx-rpi --extra-ldflags=-latomic
  libavutil      56. 55.100 / 56. 55.100
  libavcodec     58. 96.100 / 58. 96.100
  libavformat    58. 48.100 / 58. 48.100
  libavdevice    58. 11.101 / 58. 11.101
  libavfilter     7. 87.100 /  7. 87.100
  libswscale      5.  8.100 /  5.  8.100
  libswresample   3.  8.100 /  3.  8.100
  libpostproc    55.  8.100 / 55.  8.100
[video4linux2,v4l2 @ 0x2a7d2d0] Compressed:        h264 :                H.264 : 1920x1080 1280x720 640x480 352x288 320x240 1920x1080

Then you’ll need to get a list of the available sound cards

cat /proc/asound/cards

That should give a list something like:

 0 [Headphones     ]: bcm2835_headphonbcm2835 Headphones - bcm2835 Headphones
                      bcm2835 Headphones
 1 [USB            ]: USB-Audio - HD264 Webcam USB
                      HD264 Webcam USB HD264 Webcam USB at usb-0000:01:00.0-1.2, high speed
 2 [Device         ]: USB-Audio - USB PnP Audio Device
                      USB PnP Audio Device at usb-0000:01:00.0-1.4, full speed

Take note of the device name in the square braces, as that’s what we’ll need for the ffmpeg command later. In this case we want the USB sound card, and not the USB camera mic., so we want “Device”.

In the my setup the audio is only coming in over the left channel. When I watch the videos on a PC YouTube seems to manage to downmix that to a mono signal, but on phones if you're using headphones you'll only get audio out of the left channel. This could be fixed by using a stereo to mono adapter in the cabling, but it can also be done in ffmpeg by panning the audio to the left and then downmixing it to mono (which is what I've done).

Before we move on to testing, you’ll need to make sure you have your YouTube account setup for doing Live broadcasts. If you don’t already have a YouTube account setup, go ahead and create one. Then go to YouTube Studio https://studio.youtube.com/ if you’re not already setup you’ll probably need to “activate” your account and accept the broadcasts terms and conditions. Next you’ll want to click on that “Create” button that should be in the upper left of the page (at least when I’m writing this) and select “Go Live”. At this point it’s going to ask you to verify your account with a phone number (you might have problems using VoIP including Google Voice numbers for this step) after which they will have to verify your account. This process can take as little as 24 hours, but I’ve had it take a week for some accounts. Once you’re account has been verified, go back to “Go Live” and from there you’ll want to select “Classic Streaming” (there might be a way to do all of this from their new Live Streaming page, but I found the “Classic Streaming” page to be a lot easier.

From here setup your Live stream options, enable/disable chat, set Privacy mode, set the Steam optimizations. Go into Advanced Settings and make sure that’s all setup the way you like.

For my use, I set the Privacy to “Unlisted”, Stream optimizations to “Normal latency”, and then under Advanced settings I disabled Live Chat, DVR and Comments. (I basically went through and unchecked everything)

Once you’ve saved that and you’re back on the Classic Streaming menu, at the bottom left of the page is the encoder setup. Take a note of the Server URL and your Stream name/key as you’ll need both of those to send/start the broadcast.

Just a quick note on the Privacy setting. On the bottom right of the page you’ll see a link to share your live videos. If you have your privacy set to “Public” your videos should get announced on your page when you go live. People should be able to search for your videos and get notifications on new videos. If you set it to “Unlisted” then only people who have the link should be able to watch your videos, they shouldn't show up on searches and your subscribers won't get an announcment when new videos go live. In either case that share URL will change for each new Live broadcast (a broadcast is considered new, the link changes, after 1 min. has elapsed after your last stream) so I'm including scripts that will update a web page with that changing link. This should really only be needed for "Unlisted", your latest, or current Live video should be available at the URL https://www.youtube.com/channel/<Channel ID>/live. For the "Private" privacy setting, only you should be able to watch the live video.

After a whole lot of Googleing, testing, tweeking, and other attempts, this is the final command I landed on that gives a good solid stream to YouTube in 1080p at 15-20fps (the fastest framerate I was able to achieve with this setup, but for broadcasting a talk works quite well) You can run this command from the RPi and in a few seconds from the Classic Streaming page you should see your broadcast go live.

ffmpeg -thread_queue_size 2048 -f alsa -guess_layout_max 0 -i default:CARD=Device -thread_queue_size 2048  -itsoffset 1.8 -f v4l2 -framerate 15 -video_size 1920x1080 -c:v h264 -i /dev/video2 -c:v libx264 -pix_fmt yuv420p -preset superfast -g 25 -b:v 3000k -maxrate 3000k -bufsize 1500k -strict experimental -acodec libmp3lame -ar 44100 -threads 4 -q:v 5 -q:a 5 -b:a 64k -t 1:05:00 -af pan="mono: c0=FL" -ac 1 -filter:a "volume=20dB" -f flv <server URL>/<Stream Key>

The included scripts will help automate this process, and takes some of the settings as command line parameters but this should get you up and running with a basic stream.
