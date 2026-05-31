# Copyright (c) 2026 Nitish NS <nitish.ns378@gmail.com>. All rights reserved.
# Unauthorized copying, modification, or distribution of this file is prohibited.
"""
tools/get_sr_models.py — fetch pretrained super-resolution models into models/.

These are the ESPCN TensorFlow models also used by OpenCV's dnn_superres. The
receiver's Upscaler extracts their weights and runs them in PyTorch.

Usage:
    python tools/get_sr_models.py            # x2, x3, x4
    python tools/get_sr_models.py --scale 4  # just x4
"""
import argparse
import os
import urllib.request

MODEL_DIR = 'models'
BASE = 'https://raw.githubusercontent.com/fannymonori/TF-ESPCN/master/export'
MODELS = {2: 'ESPCN_x2.pb', 3: 'ESPCN_x3.pb', 4: 'ESPCN_x4.pb'}


def fetch(scale: int) -> None:
    name = MODELS[scale]
    dst  = os.path.join(MODEL_DIR, name)
    if os.path.exists(dst):
        print(f'[skip] {dst} already present ({os.path.getsize(dst)} bytes)')
        return
    url = f'{BASE}/{name}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    data = urllib.request.urlopen(req).read()
    with open(dst, 'wb') as f:
        f.write(data)
    print(f'[ok]   {dst}  ({len(data)} bytes)')


def main():
    ap = argparse.ArgumentParser(description='Download ESPCN SR models')
    ap.add_argument('--scale', type=int, choices=[2, 3, 4], default=None,
                    help='Only this scale (default: all)')
    args = ap.parse_args()

    os.makedirs(MODEL_DIR, exist_ok=True)
    scales = [args.scale] if args.scale else sorted(MODELS)
    for s in scales:
        try:
            fetch(s)
        except Exception as e:
            print(f'[fail] ESPCN_x{s}: {e}')


if __name__ == '__main__':
    main()
