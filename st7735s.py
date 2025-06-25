import spidev
import RPi.GPIO as GPIO
from PIL import Image, ImageDraw, ImageFont
import time
import logging
from typing import Optional, Tuple, List, Union

# Display command constants
_CMD_SWRESET = 0x01
_CMD_SLPOUT = 0x11
_CMD_INVON = 0x21
_CMD_NORON = 0x20
_CMD_DISPON = 0x29
_CMD_CASET = 0x2A
_CMD_RASET = 0x2B
_CMD_RAMWR = 0x2C
_CMD_MADCTL = 0x36
_CMD_COLMOD = 0x3A
_CMD_FRMCTR1 = 0xB1
_CMD_FRMCTR2 = 0xB2
_CMD_FRMCTR3 = 0xB3
_CMD_INVCTR = 0xB4
_CMD_PWCTR1 = 0xC0
_CMD_PWCTR2 = 0xC1
_CMD_PWCTR3 = 0xC2
_CMD_PWCTR4 = 0xC3
_CMD_PWCTR5 = 0xC4
_CMD_VMCTR1 = 0xC5

# MADCTL orientation bits
_MADCTL_MH = 0x04
_MADCTL_RGB = 0x00
_MADCTL_BGR = 0x08
_MADCTL_ML = 0x10
_MADCTL_MV = 0x20
_MADCTL_MX = 0x40
_MADCTL_MY = 0x80

# Color mode
_COLMOD_16BIT = 0x05

# Default display dimensions
_DISPLAY_WIDTH = 80
_DISPLAY_HEIGHT = 160

# SPI transfer limits
_MAX_SPI_CHUNK = 4096  # Max bytes per SPI transfer

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


def hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    """Convert hex color string to RGB tuple."""
    hex_color = hex_color.lstrip('#')
    if len(hex_color) != 6:
        raise ValueError("Hex color must be 6 hex digits (RRGGBB)")
    return (
        int(hex_color[0:2], 16),
        int(hex_color[2:4], 16),
        int(hex_color[4:6], 16)
    )


class ST7735S:
    """Driver for ST7735S based 0.96" 80x160 TFT display."""
    
    # Class-level font cache
    _DEFAULT_FONT = None
    
    def __init__(
        self,
        dc: int = 25,
        rst: int = 27,
        bl: int = 24,
        port: int = 0,
        cs: int = 0,
        speed_hz: int = 24_000_000,
        rotation: int = 0,
        invert: bool = False,
        x_offset: int = 24,
        y_offset: int = 0,
        debug: bool = False,
    ):
        # Configure logging
        self.logger = logging.getLogger("ST7735S")
        self.logger.setLevel(logging.DEBUG if debug else logging.INFO)
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        
        # Initialize dimensions with configurable offsets
        self.orig_width = _DISPLAY_WIDTH
        self.orig_height = _DISPLAY_HEIGHT
        self._hw_x_offset = x_offset  # Store hardware offset
        self._hw_y_offset = y_offset  # Store hardware offset
        self._x_offset = x_offset     # Active x offset
        self._y_offset = y_offset     # Active y offset
        
        # Setup GPIO
        self._dc = dc
        self._rst = rst
        self._bl = bl
        
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self._dc, GPIO.OUT)
            GPIO.setup(self._rst, GPIO.OUT)
            GPIO.setup(self._bl, GPIO.OUT)
            GPIO.output(self._bl, GPIO.LOW)
        except Exception as e:
            self.logger.exception("GPIO initialization failed")
            raise
        
        # Initialize SPI
        self.spi = spidev.SpiDev()
        try:
            self.spi.open(port, cs)
            self.spi.max_speed_hz = speed_hz
            self.spi.mode = 0
            self.spi.lsbfirst = False
        except Exception as e:
            self.logger.exception("SPI initialization failed")
            GPIO.cleanup([dc, rst, bl])
            raise
        
        # Initialize default font if not already loaded
        if ST7735S._DEFAULT_FONT is None:
            try:
                ST7735S._DEFAULT_FONT = ImageFont.truetype(
                    "DejaVuSansMono-Bold.ttf", 18
                )
                self.logger.debug("Loaded default font")
            except IOError:
                ST7735S._DEFAULT_FONT = ImageFont.load_default()
                self.logger.warning("Using fallback default font")
        
        # Initialize display
        self.reset()
        self.backlight(True)
        self._init_display(invert)
        self.set_rotation(rotation)
        self.fill((0, 0, 0))
        self.logger.info(f"Display initialized (x_offset={x_offset}, y_offset={y_offset})")

    def reset(self) -> None:
        """Perform hardware reset of the display."""
        try:
            GPIO.output(self._rst, GPIO.HIGH)
            time.sleep(0.005)
            GPIO.output(self._rst, GPIO.LOW)
            time.sleep(0.01)
            GPIO.output(self._rst, GPIO.HIGH)
            time.sleep(0.15)
        except Exception:
            self.logger.error("Reset failed", exc_info=True)

    def backlight(self, state: bool) -> None:
        """Control display backlight."""
        try:
            GPIO.output(self._bl, GPIO.HIGH if state else GPIO.LOW)
        except Exception:
            self.logger.warning("Backlight control failed", exc_info=True)

    def close(self) -> None:
        """Release resources and clean up."""
        try:
            self.spi.close()
        except Exception:
            self.logger.warning("Error closing SPI", exc_info=True)
        finally:
            try:
                GPIO.cleanup([self._dc, self._rst, self._bl])
            except Exception:
                self.logger.warning("GPIO cleanup failed", exc_info=True)
            self.logger.info("Resources released")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _write_command(self, command: int) -> None:
        """Send a command byte to the display."""
        try:
            GPIO.output(self._dc, GPIO.LOW)
            self.spi.xfer2([command])
        except Exception:
            self.logger.error("Command write failed", exc_info=True)
            self._recover_spi()

    def _write_data(self, data: Union[bytes, bytearray, list]) -> None:
        """Send data bytes to the display."""
        try:
            GPIO.output(self._dc, GPIO.HIGH)
            
            if isinstance(data, int):
                data = [data]
                
            # Send in chunks
            for i in range(0, len(data), _MAX_SPI_CHUNK):
                chunk = data[i:i + _MAX_SPI_CHUNK]
                self.spi.xfer2(chunk)
        except Exception:
            self.logger.error("Data write failed", exc_info=True)
            self._recover_spi()

    def _recover_spi(self) -> None:
        """Attempt to recover from SPI communication errors."""
        self.logger.warning("Attempting SPI recovery")
        try:
            self.spi.close()
            time.sleep(0.1)
            self.spi.open(0, 0)
            self.spi.max_speed_hz = 24_000_000
            self.reset()
            self._init_display(False)
            self.logger.info("SPI recovery successful")
        except Exception:
            self.logger.critical("SPI recovery failed", exc_info=True)

    def _init_display(self, invert: bool) -> None:
        """Initialize display with manufacturer settings."""
        try:
            # Hardware reset already done, now send init sequence
            self._write_command(_CMD_SWRESET)
            time.sleep(0.15)
            
            self._write_command(_CMD_SLPOUT)  # Exit sleep mode
            time.sleep(0.5)
            
            # Frame rate control
            self._write_command(_CMD_FRMCTR1)
            self._write_data([0x01, 0x2C, 0x2D])
            
            self._write_command(_CMD_FRMCTR2)
            self._write_data([0x01, 0x2C, 0x2D])
            
            self._write_command(_CMD_FRMCTR3)
            self._write_data([0x01, 0x2C, 0x2D, 0x01, 0x2C, 0x2D])
            
            self._write_command(_CMD_INVCTR)
            self._write_data([0x07])
            
            # Power control
            self._write_command(_CMD_PWCTR1)
            self._write_data([0xA2, 0x02, 0x84])
            
            self._write_command(_CMD_PWCTR2)
            self._write_data([0xC5])
            
            self._write_command(_CMD_PWCTR3)
            self._write_data([0x0A, 0x00])
            
            self._write_command(_CMD_PWCTR4)
            self._write_data([0x8A, 0x2A])
            
            self._write_command(_CMD_PWCTR5)
            self._write_data([0x8A, 0xEE])
            
            self._write_command(_CMD_VMCTR1)
            self._write_data([0x0E])
            
            # Color mode and inversion
            self._write_command(_CMD_INVON if invert else _CMD_NORON)
            self._write_command(_CMD_COLMOD)
            self._write_data([_COLMOD_16BIT])  # 16-bit color
            
            # Set initial window
            self._set_window(0, 0, self.orig_width - 1, self.orig_height - 1)
            
            # Wake up display
            time.sleep(0.01)
            self._write_command(_CMD_DISPON)
            time.sleep(0.1)
        except Exception:
            self.logger.critical("Display init failed", exc_info=True)
            raise

    def set_rotation(self, rotation: int) -> None:
        """Set display rotation (0, 90, 180, or 270 degrees)."""
        rotation = rotation % 360
        if rotation not in (0, 90, 180, 270):
            raise ValueError("Rotation must be 0, 90, 180, or 270")
        
        # Configure MADCTL register
        madctl = _MADCTL_RGB
        if rotation == 0:
            madctl |= _MADCTL_MX | _MADCTL_MY
            self.width, self.height = self.orig_width, self.orig_height
            # Use standard offsets
            self._x_offset = self._hw_x_offset
            self._y_offset = self._hw_y_offset
        elif rotation == 90:
            madctl |= _MADCTL_MY | _MADCTL_MV
            self.width, self.height = self.orig_height, self.orig_width
            # Swap and adjust offsets for landscape
            self._x_offset = self._hw_y_offset
            self._y_offset = self._hw_x_offset
        elif rotation == 180:
            madctl |= 0  # MX and MY not set
            self.width, self.height = self.orig_width, self.orig_height
            # Use standard offsets
            self._x_offset = self._hw_x_offset
            self._y_offset = self._hw_y_offset
        else:  # 270 degrees
            madctl |= _MADCTL_MX | _MADCTL_MV
            self.width, self.height = self.orig_height, self.orig_width
            # Swap and adjust offsets for landscape
            self._x_offset = self._hw_y_offset
            self._y_offset = self._hw_x_offset
        
        self._write_command(_CMD_MADCTL)
        self._write_data([madctl])
        
        # Update window boundaries
        self._set_window(0, 0, self.width - 1, self.height - 1)
        self.rotation = rotation
        self.logger.debug(f"Rotation set to {rotation}°")

    def _set_window(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """Set the active drawing window."""
        # Apply hardware offsets
        x0 += self._x_offset
        x1 += self._x_offset
        y0 += self._y_offset
        y1 += self._y_offset
        
        # Set column address
        self._write_command(_CMD_CASET)
        self._write_data([
            x0 >> 8, x0 & 0xFF,
            x1 >> 8, x1 & 0xFF
        ])
        
        # Set row address
        self._write_command(_CMD_RASET)
        self._write_data([
            y0 >> 8, y0 & 0xFF,
            y1 >> 8, y1 & 0xFF
        ])
        
        # Prepare for memory write
        self._write_command(_CMD_RAMWR)

    def _rgb_to_565(self, color: Union[Tuple[int, int, int], str]) -> int:
        """Convert RGB color to 16-bit 565 format with validation."""
        if isinstance(color, str):
            r, g, b = hex_to_rgb(color)
        else:
            # Clamp values to 0-255 range for safety
            r = max(0, min(255, color[0]))
            g = max(0, min(255, color[1]))
            b = max(0, min(255, color[2]))
            
        return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)

    def _image_to_data(self, image: Image.Image) -> bytes:
        """Convert PIL Image to display-ready bytes in 565 format."""
        if _HAS_NUMPY:
            arr = np.array(image.convert("RGB"))
            r = (arr[..., 0] >> 3).astype(np.uint16)
            g = (arr[..., 1] >> 2).astype(np.uint16)
            b = (arr[..., 2] >> 3).astype(np.uint16)
            rgb565 = (r << 11) | (g << 5) | b
            return rgb565.astype('>u2').tobytes()
        else:
            buffer = bytearray()
            for r, g, b in image.getdata():
                buffer.extend(self._rgb_to_565((r, g, b)).to_bytes(2, 'big'))
            return bytes(buffer)

    def display(self, image: Image.Image) -> None:
        """Display a PIL Image on the screen."""
        try:
            # Only resize if needed
            if image.size != (self.width, self.height):
                image = image.resize((self.width, self.height), Image.LANCZOS)
            
            # Convert to display-native format
            pixel_data = self._image_to_data(image)
            
            # Transfer to display
            self._set_window(0, 0, self.width - 1, self.height - 1)
            self._write_data(pixel_data)
        except Exception:
            self.logger.error("Display update failed", exc_info=True)

    def fill(self, color: Union[Tuple[int, int, int], str, int]) -> None:
        """Fill the entire display with a solid color."""
        try:
            # Convert to 565 format if needed
            if isinstance(color, (tuple, str)):
                color = self._rgb_to_565(color)
            
            # Prepare color bytes
            hi = (color >> 8) & 0xFF
            lo = color & 0xFF
            color_pair = [hi, lo]
            
            # Calculate total pixels
            total_pixels = self.width * self.height
            
            # Set drawing window
            self._set_window(0, 0, self.width - 1, self.height - 1)
            GPIO.output(self._dc, GPIO.HIGH)
            
            # Send in optimized chunks
            pixels_per_chunk = _MAX_SPI_CHUNK // 2
            for offset in range(0, total_pixels, pixels_per_chunk):
                chunk_size = min(pixels_per_chunk, total_pixels - offset)
                self.spi.xfer2(color_pair * chunk_size)
        except Exception:
            self.logger.error("Fill operation failed", exc_info=True)

    # -------------------------
    # HIGH-LEVEL DRAWING API
    # -------------------------
    
    def create_canvas(
        self,
        bg_color: Union[Tuple[int, int, int], str] = "#000000"
    ) -> Tuple[Image.Image, ImageDraw.ImageDraw]:
        """Create a full-size drawing canvas matching current rotation."""
        # Use current logical dimensions (already rotated)
        w, h = self.width, self.height
        
        # Convert color if needed
        if isinstance(bg_color, str):
            bg_color = hex_to_rgb(bg_color)
        
        # Create image
        img = Image.new("RGB", (w, h), bg_color)
        return img, ImageDraw.Draw(img)

    def draw_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        position: Tuple[int, int],
        font: Optional[ImageFont.FreeTypeFont] = None,
        color: Tuple[int, int, int] = (255, 255, 255),
        align: str = "left",
        max_width: Optional[int] = None
    ) -> None:
        """Draw text with alignment and truncation options."""
        # Use cached default font if none provided
        font = font or ST7735S._DEFAULT_FONT
        
        # Text truncation if needed
        if max_width:
            try:
                # Modern PIL version
                bbox = font.getbbox(text)
                text_width = bbox[2] - bbox[0]
            except AttributeError:
                # Legacy PIL version
                text_width, _ = draw.textsize(text, font=font)
                
            if text_width > max_width:
                while text and len(text) > 1:
                    try:
                        # Modern PIL version
                        bbox = font.getbbox(text + "...")
                        new_width = bbox[2] - bbox[0]
                    except AttributeError:
                        # Legacy PIL version
                        new_width, _ = draw.textsize(text + "...", font=font)
                        
                    if new_width <= max_width:
                        break
                    text = text[:-1]
                text += "..."
        
        # Adjust position for alignment
        x, y = position
        if align != "left":
            try:
                # Modern PIL version
                bbox = font.getbbox(text)
                text_width = bbox[2] - bbox[0]
            except AttributeError:
                # Legacy PIL version
                text_width, _ = draw.textsize(text, font=font)
                
            if align == "center":
                x -= text_width // 2
            elif align == "right":
                x -= text_width
        
        # Render text
        draw.text((x, y), text, font=font, fill=color)

    def draw_line(
        self,
        draw: ImageDraw.ImageDraw,
        start: Tuple[int, int],
        end: Tuple[int, int],
        color: Tuple[int, int, int] = (255, 255, 255),
        width: int = 1
    ) -> None:
        """Draw a straight line."""
        draw.line([start, end], fill=color, width=width)

    def draw_sparkline(
        self,
        draw: ImageDraw.ImageDraw,
        data: List[float],
        bounds: Tuple[int, int, int, int],
        color: Tuple[int, int, int] = (255, 255, 255),
        bg_color: Optional[Tuple[int, int, int]] = None,
        thickness: int = 1
    ) -> None:
        """Draw a sparkline graph within specified bounds."""
        if not data:
            return
            
        x1, y1, x2, y2 = bounds
        width = x2 - x1
        height = y2 - y1
        
        # Draw background if specified
        if bg_color:
            draw.rectangle(bounds, fill=bg_color)
        
        # Scale data to fit bounds
        min_val = min(data)
        max_val = max(data)
        val_range = max_val - min_val if max_val != min_val else 1
        
        scaled = [
            y2 - int(((val - min_val) / val_range) * height)
            for val in data
        ]
        
        # Generate points
        step = width / max(1, (len(data) - 1))
        points = [(x1 + int(i * step), scaled[i]) for i in range(len(data))]
        
        # Draw the line
        if len(points) > 1:
            draw.line(points, fill=color, width=thickness)
