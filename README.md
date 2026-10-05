# Cable Box

A Python cable box for this laptop and a Raspberry Pi. It plays video files saved on the machine, on numbered channels, synced to the clock. Tuning in joins the current program already in progress.

Videos live in one collection, each with tags. A channel is a set of time blocks. A block names the tags to play and whether to play them in order or shuffled, and the box fills that time from the collection.

## Run

From this folder:

```bash
python -m cablebox
```

Add `--fullscreen` to fill the screen. On a Pi, that is the set-top mode.

```bash
python -m cablebox --fullscreen
```

`--data` points at a different `channels.json` if you do not want the one in `data/`.

## Controls

- Up / Down: next or previous channel, in channel-number order. In the guide, Up and Down move the highlight.
- Number keys, then Enter: tune that channel
- G: show or hide the guide
- Left / Right in the guide: previous or next program
- Enter in the guide: tune the highlighted channel
- M: lineup editor
- I: show the channel banner again
- Left / Right while watching, or − and + on the volume bar: volume. Mute is on that bar. − and + keys do the same.
- F: fullscreen
- Esc: close the guide or editor, then leave fullscreen
- Q: quit

## Program a channel

Press M.

On the left, add videos and tag them. Naruto episode 1 might be tagged `naruto, anime, shounen, action, 00s`. Apply tags to every selected video. The same video can air on more than one channel.

Set a series name, such as `naruto`, and an episode number when the title alone is not enough to keep a show in order. Select the episodes in the list, type the series, set episode `1`, then press Number in order. Episode 2 plays before episode 10 inside that series, and a different series name plays as its own run. A numeric name such as `2` still comes before `10`. Videos without a series or episode follow the title.

On the right, add a channel, then add blocks. A block is a time range and the tags to play. `naruto` from 8:00 PM to 10:00 PM plays those episodes in order and continues the next night from where it left off. `00s, anime` set to Shuffle from 10:30 PM plays a new order each day, the same order for anyone watching that day. A video airs when it has every tag on the block. You do not place each episode.

A block stays inside one day. An end time of 12:00 AM means midnight. A show that runs past midnight is two blocks. Empty time between blocks is standby. Save, and the guide fills the blocks from the collection.

## Windows

1. Install 64-bit Python 3.11 or newer.
2. Download a current 64-bit mpv build that includes `mpv.exe` and either `mpv-2.dll` or `libmpv-2.dll`. In the shinchiro Windows releases, `mpv.exe` is in the `mpv-x86_64` archive and `libmpv-2.dll` is in the `mpv-dev-x86_64` archive. Put both files in one folder and add that folder to `PATH`. You can also put them in an `mpv` folder next to this app.
3. Install the Python packages:

```bash
pip install -r requirements.txt
```

The `mpv` package on PyPI is the python-mpv binding. The separate mpv download is the player itself.

## Raspberry Pi OS (64-bit)

1080p H.264 is a comfortable target on a Pi 4. The player asks mpv for `hwdec=auto-safe` so video is drawn inside the Qt window on Wayland and on X11.

```bash
sudo apt install mpv libmpv2 python3-venv python3-pip
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m cablebox --fullscreen
```

To boot into the box on Raspberry Pi OS with labwc, add this to `~/.config/labwc/autostart`, with your own path:

```bash
cd /home/pi/Code/TV
. .venv/bin/activate
python -m cablebox --fullscreen
```
