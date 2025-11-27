#!/usr/bin/python3

import argparse
import signal
import os
import traceback
import json
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from pathlib import Path

import sms # sms.py local file

# ---- SETTINGS ----
SCRIPT_DIR = os.path.abspath(os.path.dirname(__file__))
FONT_DIR = os.path.join(SCRIPT_DIR, "fonts")
TITLE_FONT    = os.path.join(FONT_DIR, "Merriweather-Bold.ttf")
SUBTITLE_FONT = os.path.join(FONT_DIR, "Merriweather-Italic.ttf")

TITLE_SIZE = 130
SUBTITLE_SIZE = 76
TEXT_COLOR = (133, 32, 12)

TITLE_START_Y = 162
SUBTITLE_START_Y = 610

TITLE_LINE_SPACING = int(TITLE_SIZE * 0.34)
SUB_LINE_SPACING   = int(SUBTITLE_SIZE * 0.34)

def draw_text_with_shadow(image, draw, text, font, x, y,
                          fill,
                          shadow_offset=(4, 4),
                          shadow_blur=6,
                          shadow_alpha=120):

    # Create a layer for shadow
    shadow_layer = Image.new("RGBA", image.size, (0,0,0,0))
    shadow_draw = ImageDraw.Draw(shadow_layer)

    # Shadow text (semi-transparent black)
    shadow_draw.text(
        (x + shadow_offset[0], y + shadow_offset[1]),
        text,
        font=font,
        fill=(0, 0, 0, shadow_alpha)
    )

    # Blur shadow
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(shadow_blur))

    # Composite shadow and real text
    image.alpha_composite(shadow_layer)

    # Draw the crisp text on top
    draw.text((x, y), text, font=font, fill=fill)

def create_card(base_image, output_image, title, subtitle, ward = None, num_from = None, num_to = None, verbose = None):
    # Use RGBA so the shadow compositing works cleanly
    img = Image.open(base_image).convert("RGBA")
    draw = ImageDraw.Draw(img)
    image_w, image_h = img.size

    try:
        font_title = ImageFont.truetype(TITLE_FONT, TITLE_SIZE)
        font_sub   = ImageFont.truetype(SUBTITLE_FONT, SUBTITLE_SIZE)
    except:
        if(verbose): print(traceback.format_exc())
        print("Error can't find fonts to create card")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to find font to create card!", verbose)
        return

    # ----- TITLE (bold) -----
    title_lines = title.split("\n")
    single_line_title = (len(title_lines) == 1)

    y = TITLE_START_Y

    # If only one line, shift it down to second-row position
    if single_line_title:
        # Measure one typical line to know the height
        bbox = draw.textbbox((0, 0), title_lines[0], font=font_title)
        text_h = bbox[3] - bbox[1]
        y += text_h + TITLE_LINE_SPACING   # move down one “line step”

    for line in title_lines:
        bbox = draw.textbbox((0, 0), line, font=font_title)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = (image_w - text_w) // 2

        draw_text_with_shadow(
            img, draw, line, font_title, x, y,
            fill=TEXT_COLOR,
            shadow_offset=(4, 4),
            shadow_blur=6,
            shadow_alpha=120
        )

        y += text_h + TITLE_LINE_SPACING

    # ----- SUBTITLE (italic) -----
    y = SUBTITLE_START_Y
    for line in subtitle.split("\n"):
        bbox = draw.textbbox((0, 0), line, font=font_sub)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = (image_w - text_w) // 2

        draw_text_with_shadow(
            img, draw, line, font_sub, x, y,
            fill=TEXT_COLOR,
            shadow_offset=(3, 3),
            shadow_blur=4,
            shadow_alpha=90
        )

        y += text_h + SUB_LINE_SPACING

    try:
        img.convert("RGB").save(output_image, quality=95)
    except:
        if(verbose): print(traceback.format_exc())
        print("Error attempting to create card")
        if(num_from is not None and num_to is not None):
            sms.send_sms(num_from, num_to, ward + " failed to create card!", verbose)
    print("Saved :", output_image)

if __name__ == '__main__':
    config_file = None

    parser = argparse.ArgumentParser(description='Dynamically create YouTube title cards')
    parser.add_argument('-c','--config-file',type=str,help='JSON Configuration file')
    parser.add_argument('-w','--ward',type=str,help='Name of Ward being broadcast')
    parser.add_argument('--card-title',type=str,help='Text to show in card title')
    parser.add_argument('--card-subtitle',type=str,help='Text to show in card subtitle')
    parser.add_argument('--card-pause-subtitle',type=str,help='Text to show in pause card subtitle')
    parser.add_argument('--base-image',type=str,help='Base image to use for card, expected to be 1080p')
    parser.add_argument('--output-image',type=str,help='Text to show in card subtitle')
    parser.add_argument('-F','--num-from',type=str,help='SMS notification from number - Twilio account number')
    parser.add_argument('-T','--num-to',type=str,help='SMS number to send notification to')
    parser.add_argument('-v','--verbose',default=False, action='store_true',help='Increases vebosity of error messages')
    args = parser.parse_args()

    ward = args.ward
    num_from = args.num_from
    num_to = args.num_to
    verbose = args.verbose
    card_title = args.card_title
    card_subtitle = args.card_subtitle
    card_pause_subtitle = args.card_pause_subtitle
    base_image = args.base_image
    output_image = args.output_image

    if(args.config_file is not None):
        if("/" in args.config_file):
            config_file = args.config_file
        else:
            config_file =  os.path.abspath(os.path.dirname(__file__)) + "/" + args.config_file
        if(verbose): print('Config file : ' + config_file)
    if(config_file is not None and os.path.exists(config_file)):
        with open(config_file, "r") as configFile:
            config = json.load(configFile)

            # check for keys in config file
            if 'broadcast_ward' in config:
                ward = config['broadcast_ward']
            if 'title_card_base_image' in config:
                base_image = config['title_card_base_image']
            if 'title_card_title' in config:
                card_title = config['title_card_title']
            if 'title_card_subtitle' in config:
                card_subtitle = config['title_card_subtitle']
            if 'title_card_pause_subtitle' in config:
                card_pause_subtitle = config['title_card_pause_subtitle']
            if 'notification_text_from' in config:
                num_from = config['notification_text_from']
            if 'notification_text_to' in config:
                num_to = config['notification_text_to']

    if(card_title is None):
        print("!!Card title is a required argument!!")
        exit()

    if(card_subtitle is None):
        print("!!Card subtitle is a required argument!!")
        exit()

    if(base_image is None):
        print("!!Base image is a required argument!!")
        exit()

    if(output_image is None):
        if(ward is None):
            print("!!Output image or Ward is a required argument!!")
            exit()
        output_image = ward.lower() + ".jpg"

    create_card(base_image, output_image, card_title, card_subtitle, ward, num_from, num_to, verbose)

    if(card_pause_subtitle is not None):
        src = Path(output_image) 
        output_image = src.with_name(f"{src.stem}_pause{src.suffix}")
        create_card(base_image, output_image, card_title, card_pause_subtitle, ward, num_from, num_to, verbose)
