import sys
import math
from dataclasses import dataclass
from typing import Dict, Tuple, Iterable, Optional, Any

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor, QFontMetricsF
from PyQt6.QtWidgets import QApplication, QWidget

import ipaddress

import time
# ----------------------------
# Quadtree model: store leaves by path -> value
# Path is a tiuple of ints, each int in {0,1,2,3}
# ----------------------------

Path = Tuple[int, ...]



def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v

def path_to_rect(path: Path) -> Tuple[float, float, float, float]:
    """
    Map a quadtree path to a world-space rectangle in [0,1]x[0,1].
    Returns (x, y, w, h).
    """
    x = 0.0
    y = 0.0
    size = 1.0
    for q in path:
        size *= 0.5
        if q == 0:      # TL
            pass
        elif q == 1:    # TR
            x += size
        elif q == 2:    # BL
            y += size
        elif q == 3:    # BR
            x += size
            y += size
        else:
            raise ValueError("Quadrant must be 0,1,2,3")
    return x, y, size, size

def coordinates_to_path(x,y):
    path = []
    if x > 0 and y > 0:
        q=0
        while x != 0.5 and y != 0.5:
            #print(f"Test: {x}, {y}")
            if x < 0.5:
                x = x*2
                dx = "l"
            elif x > 0.5 and not(x >= 1):
                x = 2*x-1
                dx = "r"
            elif x==0.5:
                dx = "l"
            else:
                dx = "l"
            if y < 0.5:
                y=y*2
                dy = "u"
            elif y > 0.5 and not(y >= 1):
                y=2*y-1
                dy = "d"
            elif y==0.5:
                dy="d"
            else:
                dy="d"

            #time.sleep(.5) 
            if dx=="l" and dy == "u":
                q=0
            elif dx=="r" and dy == "u":
                q=1
            elif dx=="l" and dy=="d":
                q=2
            elif dx=="r" and dy=="d":
                q=3
            else:
                q=0
            path.append(q)
            if y > 1 or x > 1:
                path = []
                break
    return path

def path_to_ipv6_cidr(arr):
    if len(arr) > 64:
        raise ValueError("Array too long: max 64 values (2 bits each)")
    for x in arr:
        if x < 0 or x > 3:
            raise ValueError("Array values must be in range 0–3")

    # Pack 2-bit symbols into a 128-bit integer (MSB-first)
    value = 0
    for x in arr:
        value = (value << 2) | x

    # Move packed bits to the top of 128 bits
    value <<= (128 - 2 * len(arr))

    prefix_len = 2 * len(arr)
    ipv6_str = str(ipaddress.IPv6Address(value))
    return f"{ipv6_str}/{prefix_len}"


def is_prefix(prefix: Path, full: Path) -> bool:
    return len(prefix) <= len(full) and full[:len(prefix)] == prefix


class QuadData:
    """
    Stores leaf fills: path -> value.
    If both a parent and child are present, the child "wins" visually
    because we draw deeper leaves on top.
    """
    def __init__(self) -> None:
        self.leaves: Dict[Path, Any] = {}

    def set_leaf(self, path: Iterable[int], value: Any) -> None:
        p = tuple(path)
        for q in p:
            if q not in (0, 1, 2, 3):
                raise ValueError("Path digits must be 0..3")
        self.leaves[p] = value

    def clear(self) -> None:
        self.leaves.clear()

    def iter_leaves(self):
        return self.leaves.items()

    def max_depth(self) -> int:
        if not self.leaves:
            return 0
        return max(len(p) for p in self.leaves.keys())



# ----------------------------
# Camera: world in [0,1]x[0,1]
# We keep a world rect (view) defined by center + scale.
# scale = world units per screen pixel (smaller => zoom in)
# ----------------------------

@dataclass
class Camera:
    cx: float = 0.5
    cy: float = 0.5
    scale: float = 1.0 / 800.0  # start: ~fits 1.0 world across ~800px

    def screen_to_world(self, sx: float, sy: float, w: int, h: int) -> Tuple[float, float]:
        wx = self.cx + (sx - w / 2.0) * self.scale
        wy = self.cy + (sy - h / 2.0) * self.scale
        return wx, wy

    def world_to_screen(self, wx: float, wy: float, w: int, h: int) -> Tuple[float, float]:
        sx = (wx - self.cx) / self.scale + w / 2.0
        sy = (wy - self.cy) / self.scale + h / 2.0
        return sx, sy

    def world_rect(self, w: int, h: int) -> Tuple[float, float, float, float]:
        # (x0,y0,x1,y1)
        half_w = (w / 2.0) * self.scale
        half_h = (h / 2.0) * self.scale
        return (self.cx - half_w, self.cy - half_h, self.cx + half_w, self.cy + half_h)


# ----------------------------
# Viewer widget
# ----------------------------

class QuadViewer(QWidget):
    def __init__(self, data: QuadData) -> None:
        self._agg_cache = {}  # dv -> dict[(ix,iy)] = value
        super().__init__()
        self.setWindowTitle("Quadtree Tile Viewer (paths fill at stop depth)")
        self.resize(1000, 700)

        self.data = data
        self.cam = Camera()

        self._dragging = False
        self._last_mouse_pos = None

        # display knobs
        self.min_grid_px = 250     # only draw grid lines when cell size on screen >= this
        self.max_grid_depth = 30  # cap grid depth for performance

    

    def value_to_color(self, v: Any) -> QColor:
        """
        Map leaf values -> colors.
        If v is numeric, map into a simple gradient.
        If v is a (r,g,b) tuple in 0..255, use directly.
        """
        if isinstance(v, tuple) and len(v) == 3:
            r, g, b = v
            return QColor(int(r), int(g), int(b), 255)

        if isinstance(v, (int, float)):
            # simple pleasant mapping: clamp and convert to hue-ish gradient
            t = float(v)
            # normalize roughly: tweak these if you like
            t = 0.5 + 0.5 * math.tanh(t / 5.0)
            # make a gradient (blue -> magenta -> orange)
            r = int(255 * clamp(0.2 + 1.2 * t, 0, 1))
            g = int(255 * clamp(0.1 + 0.6 * (1 - abs(t - 0.5) * 2), 0, 1))
            b = int(255 * clamp(0.9 - 0.9 * t, 0, 1))
            return QColor(r, g, b, 220)

        # fallback
        return QColor(200, 200, 200, 220)

    def paintEvent(self, event) -> None:
        
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        painter.fillRect(self.rect(), QColor(15, 15, 18))
        
        w = self.width()
        h = self.height()

        self._draw_world_boundary(painter, w, h)

        dv = min(self.max_grid_depth, self.visible_max_depth(w, h, min_px=2))

        agg = self._agg_cache.get(dv)
        if agg is None:
            agg = {}
            for path, value in self.data.iter_leaves():
                p = path[:dv]
                agg[p] = value  # if many collide, last wins (or pick a rule)
            self._agg_cache[dv] = agg

        for path, value in agg.items():
            self._draw_leaf(painter, w, h, path, value)
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        # background
        painter.fillRect(self.rect(), QColor(15, 15, 18))

        w = self.width()
        h = self.height()

        # Draw the unit world boundary [0,1]^2 (helps orientation)
        self._draw_world_boundary(painter, w, h)

        # Draw leaves (filled regions)
        # Draw shallow first, then deeper on top for refinement behavior.
        leaves_sorted = (self.data.iter_leaves())
        for path, value in leaves_sorted:
            self._draw_leaf(painter, w, h, path, value)
        """
        # Draw grid overlay to visualize subdivision
        self._draw_grid(painter, w, h)

        painter.end()

    def _draw_world_boundary(self, painter: QPainter, w: int, h: int) -> None:
        x0, y0 = self.cam.world_to_screen(0.0, 0.0, w, h)
        x1, y1 = self.cam.world_to_screen(1.0, 1.0, w, h)
        rect = QRectF(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
        painter.setPen(QPen(QColor(120, 120, 140), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

    def visible_max_depth(self, w: int, h: int, min_px: int = 2) -> int:
        """
        Maximum quadtree depth that is still visually distinguishable
        at the current zoom level.
        """
        if self.cam.scale <= 0:
            return 0

        # A cell at depth d has world size 2^-d
        # Screen size ≈ (2^-d) / scale
        # Require >= min_px screen pixels
        return max(
            0,
            int(math.floor(math.log2(1.0 / (self.cam.scale * min_px))))
        )

    def _draw_leaf(self, painter: QPainter, w: int, h: int, path: Path, value: Any) -> None:
        d_vis = min(self.max_grid_depth, self.visible_max_depth(w, h, min_px=2))
        if len(path) > d_vis:
            path = path[:d_vis]  # <-- key: render at the depth you can actually see

        x, y, s, _ = path_to_rect(path)

        # cull if not visible
        vx0, vy0, vx1, vy1 = self.cam.world_rect(w, h)
        if x + s < vx0 or x > vx1 or y + s < vy0 or y > vy1:
            return

        sx0, sy0 = self.cam.world_to_screen(x, y, w, h)
        sx1, sy1 = self.cam.world_to_screen(x + s, y + s, w, h)
        rect = QRectF(min(sx0, sx1), min(sy0, sy1), abs(sx1 - sx0), abs(sy1 - sy0))

        col = self.value_to_color(value)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(col))
        painter.drawRect(rect)

        # optional: outline leaves a bit
        painter.setPen(QPen(QColor(20, 20, 22, 220), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

    def _draw_grid(self, painter: QPainter, w: int, h: int) -> None:
        if self.cam.scale <= 0:
            return

        # choose a grid depth where cells are at least min_grid_px on screen
        max_d_by_px = int(math.floor(math.log2(1.0 / (self.cam.scale * self.min_grid_px))))
        dmax = max(0, min(self.max_grid_depth, max_d_by_px))

        vx0, vy0, vx1, vy1 = self.cam.world_rect(w, h)

        step = 2.0 ** (-dmax)
        if step <= 0:
            return

        # --- draw grid lines ---
        painter.save()
        painter.setPen(QPen(QColor(60, 60, 72, 140), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # vertical
        kx0 = int(math.floor(vx0 / step))
        kx1 = int(math.ceil(vx1 / step))
        for k in range(kx0, kx1 + 1):
            x = k * step
            sx, _ = self.cam.world_to_screen(x, 0.0, w, h)
            painter.drawLine(int(sx), 0, int(sx), h)

        # horizontal
        ky0 = int(math.floor(vy0 / step))
        ky1 = int(math.ceil(vy1 / step))
        for k in range(ky0, ky1 + 1):
            y = k * step
            _, sy = self.cam.world_to_screen(0.0, y, w, h)
            painter.drawLine(0, int(sy), w, int(sy))

        painter.restore()

        # --- draw cell labels (centered in each cell) ---
        # IMPORTANT: pixels between grid lines = step / scale (since scale is world units per pixel)
        px_step = step / self.cam.scale

        # Don't label if cells are too small on screen
        if px_step < 90:  # tweak this threshold
            return

        # limit how many labels we draw (avoid 200x200 text spam)
        # If zoomed in a lot, label every cell. If not, label every Nth cell.
        stride = 1
        if px_step < 140:
            stride = 2
        if px_step < 220:
            stride = 4

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        # readable text
        painter.setPen(QPen(QColor(230, 230, 240, 220)))
        font = painter.font()
        font.setPointSize(9)
        painter.setFont(font)
        fm = QFontMetricsF(font)

        # cell index bounds (cells, not lines)
        ix0 = int(math.floor(vx0 / step))
        ix1 = int(math.ceil(vx1 / step)) - 1
        iy0 = int(math.floor(vy0 / step))
        iy1 = int(math.ceil(vy1 / step)) - 1
        
        #print(ix0,iy0)

        # iterate cells
        for ix in range(ix0, ix1 + 1, stride):
            for iy in range(iy0, iy1 + 1, stride):
                cx = (ix + 0.5) * step
                cy = (iy + 0.5) * step
                
                ox = (ix) * step
                oy = (iy) * step
                
                path = coordinates_to_path(cx,cy)
                
                display_address = path_to_ipv6_cidr(path)

                sx, sy = self.cam.world_to_screen(cx, cy, w, h)

                # quick cull in screen space
                if sx < -50 or sx > w + 50 or sy < -50 or sy > h + 50:
                    continue

                # Choose what to label with:
                # label = f"{ix},{iy}"             # cell indices
                label = f"{display_address}"      # world coords of center (compact)

                tw = fm.horizontalAdvance(label)
                th = fm.height()

                # center the label
                rect = QRectF(sx - tw / 2 - 4, sy - th / 2 - 2, tw + 8, th + 4)

                # contrast background
                painter.fillRect(rect, QColor(0, 0, 0, 130))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

        painter.restore()
# -------- Input handlers (pan/zoom) -------- 
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._last_mouse_pos = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self._last_mouse_pos = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
    def mouseMoveEvent(self, event) -> None:
        if not self._dragging or self._last_mouse_pos is None:
            return
        pos = event.position()
        delta = pos - self._last_mouse_pos
        self._last_mouse_pos = pos # pan: moving mouse right should move camera left in world coords
        self.cam.cx -= float(delta.x()) * self.cam.scale
        self.cam.cy -= float(delta.y()) * self.cam.scale
        self.update()
    def wheelEvent(self, event) -> None:
        # Zoom under cursor.
        # angleDelta is in 1/8 degrees; typical mouse wheel step is 120.
        delta = event.angleDelta().y()
        if delta == 0:
            return

        zoom_factor = 0.9 if delta > 0 else 1.0 / 0.9

        w = self.width()
        h = self.height()
        mx = float(event.position().x())
        my = float(event.position().y())

        # world point under cursor before zoom
        wx_before, wy_before = self.cam.screen_to_world(mx, my, w, h)

        # apply zoom
        self.cam.scale *= zoom_factor

        # world point under cursor after zoom
        wx_after, wy_after = self.cam.screen_to_world(mx, my, w, h)

        # shift camera so the same world point stays under cursor
        self.cam.cx += (wx_before - wx_after)
        self.cam.cy += (wy_before - wy_after)

        self.update()




# ----------------------------
# Example usage: "array corresponds to values and fill where array stops"
# We'll interpret your "array" as a list of quadrant indices.
# The leaf is exactly that path; the region at that depth gets filled.
# ----------------------------

def prefix_to_array(address,length_cidr):
    if length_cidr%2 == 0:
        quadrant_array1 = read_pairs_from_msb(address,read_bits=length_cidr)
        quadrant_array2 = []
    else:
        print("skipping prefix not divisible by two. FIX LATER")
        quadrant_array1 = []
        quadrant_array2 = []
    return quadrant_array1, quadrant_array2


def read_pairs_from_msb(x, total_bits=128, read_bits=32):
    if read_bits % 2 != 0:
        raise ValueError("read_bits must be a multiple of 2")

    pairs = []
    start = total_bits - 2
    end = total_bits - read_bits

    for shift in range(start, end - 1, -2):
        pair = (x >> shift) & 0b11
        pairs.append(pair)

    return pairs


def main():
    data = QuadData()

    with open("prefixes.txt","r") as prefixes:
        for prefix_text in prefixes:
            prefix_text = prefix_text.strip()
            #print(prefix_text)
            prefix = ipaddress.IPv6Network(prefix_text)
            quadrant_array1, quadrant_array2 = prefix_to_array(int(prefix.network_address),prefix.prefixlen)
            #print(quadrant_array1, quadrant_array2)
            if quadrant_array1:
                data.set_leaf(quadrant_array1, 2.0)
            #    print("test1")
            if quadrant_array2:
                data.set_leaf(quadrant_array2, 2.0)
            #    print("test2")

    # A few example leaves at different depths:
    # path stops -> fill that region
    #data.set_leaf([0], 2.0)          # fill top-left quarter
    #data.set_leaf([1], -2.0)         # fill top-right quarter
    #data.set_leaf([2], 1.0)          # fill bottom-left quarter

    # deeper leaves refine inside their parent quarters
    #data.set_leaf([0, 3], 6.0)       # within TL, fill BR of TL
    #data.set_leaf([0, 3, 1], 9.0)    # within that, fill TR (even deeper)
    #data.set_leaf([1, 0, 2], -8.0)   # within TR, fill TL->BL

    # you can also pass RGB tuples directly:
    #data.set_leaf([3], (80, 200, 120))      # bottom-right quarter
    #data.set_leaf([3, 0, 0], (220, 80, 90)) # refine inside BR

    app = QApplication(sys.argv)
    viewer = QuadViewer(data)
    viewer.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

