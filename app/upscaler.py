# Copyright (c) 2026 Nitish NS <nitish.ns378@gmail.com>. All rights reserved.
# Unauthorized copying, modification, or distribution of this file is prohibited.
"""
app/upscaler.py — GCS-side scaling of the decoded video to a fixed display size.

The rover may stream any resolution (and change it live to trade quality vs.
bitrate). The GCS operator picks one display resolution; every received frame —
whatever its size — is scaled to that target here. This stage is purely
receiver-side: it never touches the transmitter or the radio path.

Methods
-------
  none     passthrough (show native received resolution)
  lanczos  classical Lanczos interpolation (zero dependencies)
  espcn    ESPCN neural super-resolution (reconstructs detail)

For ESPCN the target is reached by running the integer-scale (x2/x3/x4) model
that overshoots the target, then resizing to the exact display resolution. When
the received frame is already >= the target, it is simply downscaled (no SR).

ESPCN runs in PyTorch; weights are extracted from the OpenCV `models/ESPCN_x*.pb`
files and rebuilt with torch's PixelShuffle (sidesteps OpenCV 4.13's broken
DepthToSpace TF import). If torch or a model file is unavailable, the upscaler
transparently falls back to Lanczos.

⚠ Super-resolution reconstructs plausible high-frequency detail; it can invent
texture that is not really there. Treat the upscaled image as enhanced
situational awareness, not ground truth for navigation decisions.
"""
import math
import os
import cv2
import numpy as np

MODEL_DIR = 'models'


class Upscaler:
    def __init__(self, method: str = 'none', target_wh: tuple | None = None,
                 model_dir: str = MODEL_DIR):
        """
        method    : 'none' | 'lanczos' | 'espcn'
        target_wh : (width, height) the GCS displays at, or None for passthrough.
        """
        self.method    = method if target_wh else 'none'
        self.target    = target_wh
        self._model_dir = model_dir
        self._nets      = {}      # scale -> torch model (lazy)
        self._torch     = None

        if self.method == 'espcn':
            try:
                import torch                       # noqa: F401  (probe availability)
                self._torch = torch
                print(f"[SR] ESPCN ready (torch); target {target_wh[0]}x{target_wh[1]}.")
            except Exception as e:
                print(f"[SR] torch unavailable ({e}); falling back to Lanczos.")
                self.method = 'lanczos'
        elif self.method not in ('none', 'lanczos'):
            print(f"[SR] Unknown method '{method}'; passthrough.")
            self.method = 'none'

    # ── Public API ──────────────────────────────────────────────────────────
    def upscale(self, bgr: np.ndarray) -> np.ndarray:
        if self.method == 'none' or self.target is None:
            return bgr
        tw, th = self.target
        h, w   = bgr.shape[:2]
        if (w, h) == (tw, th):
            return bgr
        # Target not larger than source → just downscale (no SR benefit).
        if tw <= w and th <= h:
            return cv2.resize(bgr, (tw, th), interpolation=cv2.INTER_AREA)
        # Upscaling needed.
        if self.method == 'espcn':
            out = self._espcn_to(bgr, tw, th)
            if out is not None:
                return out
        # lanczos (or espcn fallback)
        return cv2.resize(bgr, (tw, th), interpolation=cv2.INTER_LANCZOS4)

    def label(self) -> str:
        if self.method == 'none' or not self.target:
            return 'none'
        return f'{self.method} -> {self.target[0]}x{self.target[1]}'

    # ── ESPCN backend ───────────────────────────────────────────────────────
    def _espcn_to(self, bgr, tw, th):
        h, w  = bgr.shape[:2]
        ratio = max(tw / w, th / h)
        scale = min(4, max(2, math.ceil(ratio)))   # nearest integer SR scale 2..4
        net   = self._get_net(scale)
        if net is None:
            return None
        sr = self._run(net, bgr)                    # -> (w*scale, h*scale)
        if sr.shape[1] != tw or sr.shape[0] != th:  # land exactly on target
            interp = cv2.INTER_AREA if tw < sr.shape[1] else cv2.INTER_LANCZOS4
            sr = cv2.resize(sr, (tw, th), interpolation=interp)
        return sr

    def _get_net(self, scale: int):
        if scale in self._nets:
            return self._nets[scale]
        try:
            self._nets[scale] = self._build(scale)
        except Exception as e:
            print(f"[SR] ESPCN x{scale} unavailable ({e}); using Lanczos for "
                  f"this scale.")
            self._nets[scale] = None
        return self._nets[scale]

    def _build(self, scale: int):
        torch = self._torch
        import torch.nn as nn

        pb = os.path.join(self._model_dir, f'ESPCN_x{scale}.pb')
        if not os.path.exists(pb):
            raise FileNotFoundError(f'{pb} (run tools/get_sr_models.py)')

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
        return model

    def _run(self, net, bgr):
        torch = self._torch
        y, cr, cb = cv2.split(cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb))
        with torch.no_grad():
            inp  = torch.from_numpy(y.astype(np.float32) / 255.0)[None, None]
            sr_y = (net(inp)[0, 0].numpy() * 255.0).clip(0, 255).astype(np.uint8)
        h, w  = sr_y.shape
        cr_up = cv2.resize(cr, (w, h), interpolation=cv2.INTER_CUBIC)
        cb_up = cv2.resize(cb, (w, h), interpolation=cv2.INTER_CUBIC)
        return cv2.cvtColor(cv2.merge([sr_y, cr_up, cb_up]), cv2.COLOR_YCrCb2BGR)
