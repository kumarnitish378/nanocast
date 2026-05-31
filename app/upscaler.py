# Copyright (c) 2026 Nitish NS <nitish.ns378@gmail.com>. All rights reserved.
# Unauthorized copying, modification, or distribution of this file is prohibited.
"""
app/upscaler.py — GCS-side super-resolution for the decoded video.

The rover sends tiny frames (e.g. 90p) to keep the radio bitrate low; the GCS
reconstructs a larger, sharper image here. This stage is purely receiver-side —
it never touches the transmitter or the radio path.

Backends
--------
  none     passthrough (no scaling)
  lanczos  classical Lanczos interpolation (zero dependencies, always available)
  espcn    ESPCN neural super-resolution (real detail reconstruction)

ESPCN runs in PyTorch. The trained weights are loaded from the OpenCV
`models/ESPCN_x{scale}.pb` files (fetch them with tools/get_sr_models.py): the
conv weights are extracted via cv2.dnn and rebuilt with torch's PixelShuffle,
which sidesteps OpenCV 4.13's broken DepthToSpace TF import. Fast on CPU
(hundreds of fps at 90p). If torch or the model file is unavailable, the
upscaler transparently falls back to Lanczos.

⚠ Super-resolution reconstructs plausible high-frequency detail; it can invent
texture that is not really there. Treat the upscaled image as enhanced
situational awareness, not ground truth for navigation decisions.
"""
import os
import cv2
import numpy as np

MODEL_DIR = 'models'


class Upscaler:
    def __init__(self, method: str = 'none', scale: int = 4,
                 model_dir: str = MODEL_DIR):
        self.method = method
        self.scale  = scale
        self._net   = None          # torch ESPCN, lazily built

        if method == 'espcn':
            try:
                self._build_espcn(model_dir, scale)
                print(f"[SR] ESPCN x{scale} ready (torch).")
            except Exception as e:
                print(f"[SR] ESPCN unavailable ({e}); falling back to Lanczos.")
                self.method = 'lanczos'
        elif method not in ('none', 'lanczos'):
            print(f"[SR] Unknown method '{method}'; using passthrough.")
            self.method = 'none'

    # ── Public API ──────────────────────────────────────────────────────────
    def upscale(self, bgr: np.ndarray) -> np.ndarray:
        if self.method == 'none':
            return bgr
        if self.method == 'espcn' and self._net is not None:
            return self._espcn(bgr)
        # lanczos
        h, w = bgr.shape[:2]
        return cv2.resize(bgr, (w * self.scale, h * self.scale),
                          interpolation=cv2.INTER_LANCZOS4)

    def label(self) -> str:
        return 'none' if self.method == 'none' else f'{self.method} x{self.scale}'

    # ── ESPCN backend ───────────────────────────────────────────────────────
    def _build_espcn(self, model_dir: str, scale: int) -> None:
        import torch
        import torch.nn as nn

        pb = os.path.join(model_dir, f'ESPCN_x{scale}.pb')
        if not os.path.exists(pb):
            raise FileNotFoundError(f'{pb} (run tools/get_sr_models.py)')

        # Pull trained weights out of the .pb via cv2.dnn (the DepthToSpace
        # layer won't execute on OpenCV 4.13, but the conv weights load fine).
        net = cv2.dnn.readNetFromTensorflow(pb)
        W = {}
        for n in net.getLayerNames():
            try:
                layer = net.getLayer(net.getLayerId(n))
                if layer.blobs:
                    W[n] = [b.copy() for b in layer.blobs]
            except cv2.error:
                pass   # skip the unparseable DepthToSpace layer

        class ESPCN(nn.Module):
            def __init__(self, s):
                super().__init__()
                self.c1 = nn.Conv2d(1, 64, 5, padding=2)
                self.c2 = nn.Conv2d(64, 32, 3, padding=1)
                self.c3 = nn.Conv2d(32, s * s, 3, padding=1)
                self.ps = nn.PixelShuffle(s)

            def forward(self, x):
                x = torch.relu(self.c1(x))
                x = torch.relu(self.c2(x))
                return self.ps(self.c3(x))

        model = ESPCN(scale).eval()

        def load(conv, wkey, bkey):
            conv.weight.data = torch.tensor(W[wkey][0])
            conv.bias.data   = torch.tensor(W[bkey][0].reshape(-1))

        load(model.c1, 'conv1', 'add')
        load(model.c2, 'conv2', 'add_1')
        load(model.c3, 'conv3', 'add_2')

        self._torch = torch
        self._net   = model

    def _espcn(self, bgr: np.ndarray) -> np.ndarray:
        torch = self._torch
        y, cr, cb = cv2.split(cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb))
        with torch.no_grad():
            inp  = torch.from_numpy(y.astype(np.float32) / 255.0)[None, None]
            sr_y = (self._net(inp)[0, 0].numpy() * 255.0).clip(0, 255).astype(np.uint8)
        h, w   = sr_y.shape
        cr_up  = cv2.resize(cr, (w, h), interpolation=cv2.INTER_CUBIC)
        cb_up  = cv2.resize(cb, (w, h), interpolation=cv2.INTER_CUBIC)
        return cv2.cvtColor(cv2.merge([sr_y, cr_up, cb_up]), cv2.COLOR_YCrCb2BGR)
