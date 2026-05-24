/*
 * Macro Recorder — Arduino Pro Micro / Leonardo HID firmware
 * ===========================================================
 *
 * Boards with native USB (ATmega32U4) expose themselves as a real USB
 * keyboard + mouse via the built-in Keyboard.h / Mouse.h libraries.
 *
 * INSTALL
 * -------
 * 1. Arduino IDE → Boards Manager → install "Arduino AVR Boards"
 *    Tools → Board → "Arduino Leonardo" (also covers Pro Micro)
 *    Tools → Port → the COM port of the board
 *
 * 2. Upload this sketch.  After upload the board enumerates as both a
 *    serial port AND an HID keyboard/mouse.
 *
 * 3. Note the serial COM port (Tools → Port).  Set it in macro_recorder.py:
 *       SERIAL_HID_PORT = "COM7"
 *    or via env var MACRO_SERIAL_PORT.
 *
 * WIRE PROTOCOL  (host → device, one ASCII line each, \n-terminated)
 * ------------------------------------------------------------------
 *    KD <vk>           key down  (vk = Windows virtual-key code)
 *    KU <vk>           key up
 *    MA <x> <y>        mouse move absolute (host coords; we walk to them)
 *    MR <dx> <dy>      mouse move relative
 *    MD L|R|M          mouse button down
 *    MU L|R|M          mouse button up
 *    MW <dx> <dy>      mouse wheel scroll  (vertical = dy)
 *    PING              replies "PONG"
 *    RESET             release all keys/buttons
 *
 * Firmware prints "READY" once on USB serial after boot.
 */

#include <Keyboard.h>
#include <Mouse.h>

// ── Tracked cursor position so we can fulfil absolute moves ───────────
long curX = 0, curY = 0;

// ── Map Windows VK codes → Arduino Keyboard.h codes ───────────────────
// (Returns 0 for "unknown / unmapped"; many printable keys map to ASCII.)
uint8_t vkToKey(int vk) {
  // Letters: VK 0x41..0x5A → 'A'..'Z'
  if (vk >= 0x41 && vk <= 0x5A) return (uint8_t)('a' + (vk - 0x41));
  // Digits: VK 0x30..0x39 → '0'..'9'
  if (vk >= 0x30 && vk <= 0x39) return (uint8_t)('0' + (vk - 0x30));
  // Function keys: VK 0x70 (F1) .. 0x7B (F12)
  if (vk >= 0x70 && vk <= 0x7B) return KEY_F1 + (vk - 0x70);
  switch (vk) {
    case 0x08: return KEY_BACKSPACE;
    case 0x09: return KEY_TAB;
    case 0x0D: return KEY_RETURN;
    case 0x10: return KEY_LEFT_SHIFT;
    case 0x11: return KEY_LEFT_CTRL;
    case 0x12: return KEY_LEFT_ALT;
    case 0x14: return KEY_CAPS_LOCK;
    case 0x1B: return KEY_ESC;
    case 0x20: return ' ';
    case 0x21: return KEY_PAGE_UP;
    case 0x22: return KEY_PAGE_DOWN;
    case 0x23: return KEY_END;
    case 0x24: return KEY_HOME;
    case 0x25: return KEY_LEFT_ARROW;
    case 0x26: return KEY_UP_ARROW;
    case 0x27: return KEY_RIGHT_ARROW;
    case 0x28: return KEY_DOWN_ARROW;
    case 0x2D: return KEY_INSERT;
    case 0x2E: return KEY_DELETE;
    case 0xBA: return ';';
    case 0xBB: return '=';
    case 0xBC: return ',';
    case 0xBD: return '-';
    case 0xBE: return '.';
    case 0xBF: return '/';
    case 0xC0: return '`';
    case 0xDB: return '[';
    case 0xDC: return '\\';
    case 0xDD: return ']';
    case 0xDE: return '\'';
  }
  return 0;
}

// Walk the cursor in -127..+127 steps until we reach (tx,ty)
void mouseGoto(long tx, long ty) {
  long dx = tx - curX, dy = ty - curY;
  while (dx != 0 || dy != 0) {
    int sx = constrain(dx, -127, 127);
    int sy = constrain(dy, -127, 127);
    Mouse.move(sx, sy);
    dx -= sx; dy -= sy;
  }
  curX = tx; curY = ty;
}

int btnFromChar(char c) {
  switch (c) {
    case 'L': case 'l': return MOUSE_LEFT;
    case 'R': case 'r': return MOUSE_RIGHT;
    case 'M': case 'm': return MOUSE_MIDDLE;
  }
  return 0;
}

// ── Command parsing ──────────────────────────────────────────────────
String buf;

void handleLine(String& line) {
  line.trim();
  if (line.length() == 0) return;
  // Split into up to 3 tokens
  int s1 = line.indexOf(' ');
  String cmd = (s1 < 0) ? line : line.substring(0, s1);
  String rest = (s1 < 0) ? "" : line.substring(s1 + 1);
  int s2 = rest.indexOf(' ');
  String a1 = (s2 < 0) ? rest : rest.substring(0, s2);
  String a2 = (s2 < 0) ? ""   : rest.substring(s2 + 1);

  if (cmd == "KD") {
    uint8_t k = vkToKey(a1.toInt());
    if (k) Keyboard.press(k);
  } else if (cmd == "KU") {
    uint8_t k = vkToKey(a1.toInt());
    if (k) Keyboard.release(k);
  } else if (cmd == "MA") {
    mouseGoto(a1.toInt(), a2.toInt());
  } else if (cmd == "MR") {
    int dx = a1.toInt(), dy = a2.toInt();
    int sx = constrain(dx, -127, 127);
    int sy = constrain(dy, -127, 127);
    Mouse.move(sx, sy);
    curX += dx; curY += dy;
  } else if (cmd == "MD") {
    int b = btnFromChar(a1.length() ? a1[0] : 'L');
    if (b) Mouse.press(b);
  } else if (cmd == "MU") {
    int b = btnFromChar(a1.length() ? a1[0] : 'L');
    if (b) Mouse.release(b);
  } else if (cmd == "MW") {
    Mouse.move(0, 0, (signed char)a2.toInt());   // wheel only
  } else if (cmd == "PING") {
    Serial.println("PONG");
  } else if (cmd == "RESET") {
    Keyboard.releaseAll();
    Mouse.release(MOUSE_LEFT);
    Mouse.release(MOUSE_RIGHT);
    Mouse.release(MOUSE_MIDDLE);
  }
}

void setup() {
  Serial.begin(115200);
  Keyboard.begin();
  Mouse.begin();
  delay(200);
  Serial.println("READY");
}

void loop() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      handleLine(buf);
      buf = "";
    } else {
      buf += c;
      if (buf.length() > 96) buf = "";   // safety
    }
  }
}
