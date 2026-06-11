import argparse
import asyncio
import json
import logging
import uuid
from typing import Dict, Optional

import numpy as np
import pyautogui
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.media import MediaRelay
from av import VideoFrame
from mss import mss


# =========================
# 기본 설정
# =========================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webrtc-screen-control")

# pyautogui 안전 옵션
# True면 마우스를 화면 좌상단 모서리로 가져가면 FailSafe 예외가 날 수 있습니다.
# 원격 제어 시 불편할 수 있어 False로 두되, 테스트 환경에서만 사용 권장합니다.
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0


# =========================
# HTML / JS 페이지
# =========================

HTML_PAGE = r"""
<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>WebRTC 화면 공유 + 제어</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: #0b0f14;
      color: #e6edf3;
      font-family: Arial, sans-serif;
      display: flex;
      flex-direction: column;
      height: 100vh;
    }

    .topbar {
      padding: 12px 16px;
      background: #111827;
      border-bottom: 1px solid #1f2937;
      display: flex;
      flex-wrap: wrap;
      gap: 8px 12px;
      align-items: center;
    }

    .topbar label {
      font-size: 13px;
      color: #cbd5e1;
      display: flex;
      align-items: center;
      gap: 6px;
    }

    .topbar input[type="text"],
    .topbar input[type="password"] {
      height: 34px;
      padding: 0 10px;
      border-radius: 8px;
      border: 1px solid #334155;
      background: #0f172a;
      color: #e2e8f0;
      outline: none;
      min-width: 180px;
    }

    .topbar input[type="checkbox"] {
      transform: translateY(1px);
    }

    .topbar button {
      height: 36px;
      padding: 0 14px;
      border-radius: 10px;
      border: none;
      background: #2563eb;
      color: white;
      font-weight: 700;
      cursor: pointer;
    }

    .topbar button:hover {
      background: #1d4ed8;
    }

    .status {
      font-size: 13px;
      color: #93c5fd;
      margin-left: auto;
    }

    .main {
      flex: 1;
      display: flex;
      flex-direction: column;
      min-height: 0;
    }

    .help {
      padding: 8px 16px;
      font-size: 13px;
      color: #94a3b8;
      border-bottom: 1px solid #1f2937;
      background: #0f172a;
    }

    .video-wrap {
      flex: 1;
      min-height: 0;
      display: flex;
      justify-content: center;
      align-items: center;
      background: #000;
      padding: 12px;
    }

    video {
      max-width: 100%;
      max-height: 100%;
      border-radius: 14px;
      background: #000;
      box-shadow: 0 10px 30px rgba(0,0,0,0.45);
      outline: none;
    }

    .pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      height: 28px;
      padding: 0 10px;
      border-radius: 999px;
      background: #1e293b;
      color: #cbd5e1;
      font-size: 12px;
    }

    .ok {
      color: #22c55e;
    }

    .warn {
      color: #f59e0b;
    }

    .danger {
      color: #ef4444;
    }
  </style>
</head>
<body>
  <div class="topbar">
    <label>
      보기 토큰
      <input id="accessToken" type="password" placeholder="access token" />
    </label>

    <label>
      제어 토큰
      <input id="controlToken" type="password" placeholder="control token (선택)" />
    </label>

    <label>
      <input id="requestControl" type="checkbox" />
      제어권 요청
    </label>

    <button id="startBtn">연결 시작</button>
    <button id="disconnectBtn">연결 종료</button>

    <span id="status" class="status">대기 중</span>
  </div>

  <div class="help">
    <span class="pill">최대 동시 접속 2명</span>
    <span class="pill">제어권은 1명만 가능</span>
    <span class="pill" id="controlBadge">제어 상태: 없음</span>
    <span class="pill">영상 위에서 클릭 후 마우스/키보드 입력 가능</span>
  </div>

  <div class="main">
    <div class="video-wrap">
      <video id="video" autoplay playsinline controls></video>
    </div>
  </div>

  <script>
    let pc = null;
    let controlChannel = null;
    let hasControl = false;
    let lastMouseMoveTs = 0;
    let keyState = new Set();

    const videoEl = document.getElementById("video");
    const statusEl = document.getElementById("status");
    const controlBadgeEl = document.getElementById("controlBadge");

    function setStatus(text, cls = "") {
      statusEl.textContent = text;
      statusEl.className = "status " + cls;
    }

    function updateControlBadge() {
      controlBadgeEl.textContent = "제어 상태: " + (hasControl ? "활성" : "없음");
      controlBadgeEl.className = "pill " + (hasControl ? "ok" : "");
    }

    function waitForIceGatheringComplete(pc) {
      return new Promise((resolve) => {
        if (pc.iceGatheringState === "complete") {
          resolve();
          return;
        }

        function checkState() {
          if (pc.iceGatheringState === "complete") {
            pc.removeEventListener("icegatheringstatechange", checkState);
            resolve();
          }
        }

        pc.addEventListener("icegatheringstatechange", checkState);
      });
    }

    function normalizePointerFromEvent(event) {
      const rect = videoEl.getBoundingClientRect();
      const x = (event.clientX - rect.left) / rect.width;
      const y = (event.clientY - rect.top) / rect.height;

      return {
        x: Math.max(0, Math.min(1, x)),
        y: Math.max(0, Math.min(1, y))
      };
    }

    function sendControlMessage(obj) {
      if (!controlChannel || controlChannel.readyState !== "open") return;
      if (!hasControl) return;
      controlChannel.send(JSON.stringify(obj));
    }

    function bindInputEvents() {
      // 영상 클릭 시 포커스 확보
      videoEl.tabIndex = 0;
      videoEl.addEventListener("click", () => {
        videoEl.focus();
      });

      // 우클릭 메뉴 방지
      videoEl.addEventListener("contextmenu", (e) => {
        e.preventDefault();
      });

      // 마우스 이동 (과도한 전송 방지를 위해 20ms 제한)
      videoEl.addEventListener("mousemove", (e) => {
        if (!hasControl) return;

        const now = performance.now();
        if (now - lastMouseMoveTs < 20) return;
        lastMouseMoveTs = now;

        const p = normalizePointerFromEvent(e);
        sendControlMessage({
          type: "mouse_move",
          x: p.x,
          y: p.y
        });
      });

      // 마우스 버튼
      function mouseButtonName(button) {
        if (button === 0) return "left";
        if (button === 1) return "middle";
        if (button === 2) return "right";
        return "left";
      }

      videoEl.addEventListener("mousedown", (e) => {
        if (!hasControl) return;
        e.preventDefault();
        const p = normalizePointerFromEvent(e);
        sendControlMessage({
          type: "mouse_down",
          x: p.x,
          y: p.y,
          button: mouseButtonName(e.button)
        });
      });

      videoEl.addEventListener("mouseup", (e) => {
        if (!hasControl) return;
        e.preventDefault();
        const p = normalizePointerFromEvent(e);
        sendControlMessage({
          type: "mouse_up",
          x: p.x,
          y: p.y,
          button: mouseButtonName(e.button)
        });
      });

      // 휠 스크롤
      videoEl.addEventListener("wheel", (e) => {
        if (!hasControl) return;
        e.preventDefault();
        sendControlMessage({
          type: "mouse_wheel",
          deltaY: e.deltaY
        });
      }, { passive: false });

      // 키보드 입력
      // 브라우저 기본 동작과 충돌 가능한 키는 일부 막습니다.
      document.addEventListener("keydown", (e) => {
        if (!hasControl) return;

        // 입력 중복 전송 방지
        if (keyState.has(e.code)) return;
        keyState.add(e.code);

        // 브라우저 단축키 영향 최소화
        const reserved = ["F5"];
        if (reserved.includes(e.key)) {
          e.preventDefault();
        }

        sendControlMessage({
          type: "key_down",
          key: e.key,
          code: e.code
        });
      });

      document.addEventListener("keyup", (e) => {
        if (!hasControl) return;
        keyState.delete(e.code);

        sendControlMessage({
          type: "key_up",
          key: e.key,
          code: e.code
        });
      });

      window.addEventListener("blur", () => {
        keyState.clear();
      });
    }

    async function start() {
      const accessToken = document.getElementById("accessToken").value.trim();
      const controlToken = document.getElementById("controlToken").value.trim();
      const requestControl = document.getElementById("requestControl").checked;

      if (!accessToken) {
        setStatus("보기 토큰을 입력하세요.", "danger");
        return;
      }

      try {
        await disconnect(false);

        setStatus("RTCPeerConnection 생성 중...");
        hasControl = false;
        updateControlBadge();

        pc = new RTCPeerConnection({
          iceServers: []
        });

        // 비디오 수신만
        pc.addTransceiver("video", { direction: "recvonly" });

        // 제어용 데이터채널 생성
        controlChannel = pc.createDataChannel("control");

        controlChannel.onopen = () => {
          setStatus("데이터채널 연결됨", "ok");
        };

        controlChannel.onclose = () => {
          setStatus("데이터채널 종료", "warn");
          hasControl = false;
          updateControlBadge();
        };

        controlChannel.onmessage = (event) => {
          try {
            const msg = JSON.parse(event.data);

            if (msg.type === "control_status") {
              hasControl = !!msg.granted;
              updateControlBadge();

              if (hasControl) {
                setStatus("영상 연결 완료 / 제어권 활성", "ok");
              } else {
                setStatus("영상 연결 완료 / 보기 전용", "warn");
              }
            } else if (msg.type === "server_info") {
              console.log("server_info:", msg);
            } else if (msg.type === "error") {
              setStatus("서버 오류: " + msg.message, "danger");
            }
          } catch (err) {
            console.error(err);
          }
        };

        pc.ontrack = (event) => {
          videoEl.srcObject = event.streams[0];
        };

        pc.onconnectionstatechange = () => {
          setStatus("connectionState = " + pc.connectionState);
        };

        pc.oniceconnectionstatechange = () => {
          setStatus("iceConnectionState = " + pc.iceConnectionState);
        };

        setStatus("Offer 생성 중...");
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);

        setStatus("ICE 수집 중...");
        await waitForIceGatheringComplete(pc);

        setStatus("서버에 Offer 전송 중...");
        const res = await fetch("/offer", {
          method: "POST",
          headers: {
            "Content-Type": "application/json"
          },
          body: JSON.stringify({
            sdp: pc.localDescription.sdp,
            type: pc.localDescription.type,
            access_token: accessToken,
            control_token: controlToken,
            request_control: requestControl
          })
        });

        if (!res.ok) {
          const text = await res.text();
          throw new Error("서버 응답 오류: " + res.status + " / " + text);
        }

        const answer = await res.json();

        await pc.setRemoteDescription(new RTCSessionDescription({
          sdp: answer.sdp,
          type: answer.type
        }));

        hasControl = !!answer.control_granted;
        updateControlBadge();

        if (hasControl) {
          setStatus("연결 성공 / 제어권 활성", "ok");
        } else {
          setStatus("연결 성공 / 보기 전용", "warn");
        }
      } catch (err) {
        console.error(err);
        setStatus("오류: " + err.message, "danger");
      }
    }

    async function disconnect(updateMessage = true) {
      try {
        if (controlChannel) {
          try { controlChannel.close(); } catch (_) {}
          controlChannel = null;
        }

        if (pc) {
          try {
            pc.getSenders().forEach(s => {
              try { s.track && s.track.stop && s.track.stop(); } catch (_) {}
            });
          } catch (_) {}

          try { pc.close(); } catch (_) {}
          pc = null;
        }

        videoEl.srcObject = null;
        hasControl = false;
        keyState.clear();
        updateControlBadge();

        if (updateMessage) {
          setStatus("연결 종료됨");
        }
      } catch (err) {
        console.error(err);
        setStatus("종료 중 오류: " + err.message, "danger");
      }
    }

    document.getElementById("startBtn").addEventListener("click", start);
    document.getElementById("disconnectBtn").addEventListener("click", () => disconnect(true));

    bindInputEvents();
    updateControlBadge();
  </script>
</body>
</html>
"""


# =========================
# 화면 캡처 트랙
# =========================

class ScreenCaptureTrack(VideoStreamTrack):
    """
    모니터 화면을 주기적으로 캡처해서 WebRTC 비디오 트랙으로 내보내는 클래스
    """
    kind = "video"

    def __init__(self, monitor_index: int = 1, fps: int = 15, scale: int = 2):
        super().__init__()
        self.monitor_index = monitor_index
        self.fps = fps
        self.scale = max(1, scale)
        self.sct = mss()

        monitors = self.sct.monitors
        if monitor_index < 1 or monitor_index >= len(monitors):
            raise ValueError(
                f"잘못된 monitor_index={monitor_index}. 사용 가능한 모니터 범위: 1 ~ {len(monitors) - 1}"
            )

        self.monitor = monitors[monitor_index]
        logger.info("캡처 대상 모니터: %s", self.monitor)

    async def recv(self):
        # aiortc의 타임스탬프 생성
        pts, time_base = await self.next_timestamp()

        # FPS 제어
        await asyncio.sleep(1 / self.fps)

        # 화면 캡처 (BGRA)
        img = np.array(self.sct.grab(self.monitor), dtype=np.uint8)

        # 성능 최적화를 위해 스케일 다운
        # BGRA -> BGR
        frame = img[::self.scale, ::self.scale, :3]

        video_frame = VideoFrame.from_ndarray(frame, format="bgr24")
        video_frame.pts = pts
        video_frame.time_base = time_base
        return video_frame


# =========================
# 입력 이벤트 처리 유틸
# =========================

def safe_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default


def normalize_to_screen(nx: float, ny: float, monitor_info: dict):
    """
    브라우저에서 받은 정규화 좌표(0~1)를 실제 모니터 좌표로 변환
    """
    nx = max(0.0, min(1.0, float(nx)))
    ny = max(0.0, min(1.0, float(ny)))

    left = monitor_info["left"]
    top = monitor_info["top"]
    width = monitor_info["width"]
    height = monitor_info["height"]

    x = left + int(nx * width)
    y = top + int(ny * height)

    return x, y


def map_key_for_pyautogui(key: str, code: str) -> Optional[str]:
    """
    브라우저 키 값을 pyautogui용 키 이름으로 변환
    """
    # 자주 쓰는 특수키 매핑
    key_map = {
        "Alt": "alt",
        "AltGraph": "altright",
        "CapsLock": "capslock",
        "Control": "ctrl",
        "Shift": "shift",
        "Meta": "win",
        "OS": "win",
        "Enter": "enter",
        "Tab": "tab",
        "Escape": "esc",
        "Backspace": "backspace",
        "Delete": "delete",
        "Insert": "insert",
        "Home": "home",
        "End": "end",
        "PageUp": "pageup",
        "PageDown": "pagedown",
        "ArrowUp": "up",
        "ArrowDown": "down",
        "ArrowLeft": "left",
        "ArrowRight": "right",
        " ": "space",
        "Spacebar": "space",
        "PrintScreen": "printscreen",
    }

    if key in key_map:
        return key_map[key]

    # function key
    if code and code.startswith("F") and code[1:].isdigit():
        return code.lower()

    # 숫자/문자 1글자
    if isinstance(key, str) and len(key) == 1:
        return key.lower()

    # code 기반 보조 처리
    if code:
        # 예: KeyA -> a, Digit1 -> 1, Numpad1 -> num1 비슷한 형태는 pyautogui 대응 애매함
        if code.startswith("Key") and len(code) == 4:
            return code[-1].lower()
        if code.startswith("Digit") and len(code) == 6:
            return code[-1]

    return None


def perform_input_message(msg: dict, monitor_info: dict):
    """
    데이터채널로 받은 입력 이벤트를 실제 OS 입력으로 반영
    """
    msg_type = msg.get("type")

    if msg_type == "mouse_move":
        x, y = normalize_to_screen(msg.get("x", 0), msg.get("y", 0), monitor_info)
        pyautogui.moveTo(x, y)

    elif msg_type == "mouse_down":
        x, y = normalize_to_screen(msg.get("x", 0), msg.get("y", 0), monitor_info)
        button = msg.get("button", "left")
        pyautogui.mouseDown(x=x, y=y, button=button)

    elif msg_type == "mouse_up":
        x, y = normalize_to_screen(msg.get("x", 0), msg.get("y", 0), monitor_info)
        button = msg.get("button", "left")
        pyautogui.mouseUp(x=x, y=y, button=button)

    elif msg_type == "mouse_wheel":
        # 브라우저 deltaY는 보통 아래로 스크롤 시 양수
        # pyautogui.scroll은 양수=위, 음수=아래
        delta_y = float(msg.get("deltaY", 0))
        scroll_amount = int(-delta_y / 10)
        if scroll_amount == 0:
            scroll_amount = -1 if delta_y > 0 else 1
        pyautogui.scroll(scroll_amount)

    elif msg_type == "key_down":
        key_name = map_key_for_pyautogui(msg.get("key"), msg.get("code"))
        if key_name:
            pyautogui.keyDown(key_name)

    elif msg_type == "key_up":
        key_name = map_key_for_pyautogui(msg.get("key"), msg.get("code"))
        if key_name:
            pyautogui.keyUp(key_name)


# =========================
# 앱 생성
# =========================

def create_app(args):
    app = web.Application()

    # 공용 상태
    app["pcs"] = set()                  # 활성 PeerConnection 집합
    app["pc_meta"] = {}                 # pc_id -> 메타 정보
    app["controller_id"] = None         # 현재 제어권 보유 pc_id
    app["max_clients"] = 2
    app["access_token"] = args.access_token
    app["control_token"] = args.control_token

    # 화면 캡처 소스는 1개만 만들고, MediaRelay로 fan-out
    app["screen_source"] = ScreenCaptureTrack(
        monitor_index=args.monitor,
        fps=args.fps,
        scale=args.scale,
    )
    app["relay"] = MediaRelay()

    # 캡처 대상 모니터 정보
    app["monitor_info"] = app["screen_source"].monitor

    async def index(request):
        return web.Response(text=HTML_PAGE, content_type="text/html")

    async def health(request):
        current_count = len(app["pcs"])
        controller_id = app["controller_id"]
        data = {
            "ok": True,
            "current_clients": current_count,
            "max_clients": app["max_clients"],
            "controller_active": controller_id is not None,
            "monitor": app["monitor_info"],
        }
        return web.json_response(data)

    async def offer(request):
        # 최대 접속자 제한
        if len(app["pcs"]) >= app["max_clients"]:
            return web.Response(
                status=429,
                text="최대 동시 접속자 수(2명)를 초과했습니다."
            )

        try:
            params = await request.json()
        except Exception:
            return web.Response(status=400, text="잘못된 JSON 요청입니다.")

        # 토큰 검증
        access_token = params.get("access_token", "")
        request_control = bool(params.get("request_control", False))
        control_token = params.get("control_token", "")

        if access_token != app["access_token"]:
            return web.Response(status=401, text="보기 토큰이 올바르지 않습니다.")

        control_granted = False
        if request_control:
            # 제어 토큰이 맞고, 아직 제어권자가 없을 때만 부여
            if control_token == app["control_token"] and app["controller_id"] is None:
                control_granted = True
            else:
                control_granted = False

        offer = RTCSessionDescription(
            sdp=params["sdp"],
            type=params["type"]
        )

        pc = RTCPeerConnection()
        pc_id = str(uuid.uuid4())

        app["pcs"].add(pc)
        app["pc_meta"][pc_id] = {
            "pc": pc,
            "is_controller": False,
        }

        if control_granted:
            app["controller_id"] = pc_id
            app["pc_meta"][pc_id]["is_controller"] = True

        logger.info("Peer 생성: %s / 현재 접속자 수=%d", pc_id, len(app["pcs"]))

        # 연결 상태 변화 처리
        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            logger.info("Peer %s connectionState=%s", pc_id, pc.connectionState)
            if pc.connectionState in ("failed", "closed", "disconnected"):
                await cleanup_peer(app, pc_id)

        # 브라우저가 만든 데이터채널 수신
        @pc.on("datachannel")
        def on_datachannel(channel):
            logger.info("Peer %s datachannel 수신: %s", pc_id, channel.label)

            @channel.on("open")
            def on_open():
                logger.info("Peer %s datachannel open", pc_id)
                try:
                    channel.send(json.dumps({
                        "type": "control_status",
                        "granted": app["pc_meta"].get(pc_id, {}).get("is_controller", False)
                    }))
                    channel.send(json.dumps({
                        "type": "server_info",
                        "message": "연결 성공"
                    }))
                except Exception as e:
                    logger.warning("Peer %s datachannel send 실패: %s", pc_id, e)

            @channel.on("message")
            def on_message(message):
                try:
                    if isinstance(message, bytes):
                        return

                    msg = json.loads(message)

                    # 제어권자가 아니면 입력 무시
                    is_controller = app["pc_meta"].get(pc_id, {}).get("is_controller", False)
                    if not is_controller:
                        try:
                            channel.send(json.dumps({
                                "type": "error",
                                "message": "제어 권한이 없습니다."
                            }))
                        except Exception:
                            pass
                        return

                    perform_input_message(msg, app["monitor_info"])

                except Exception as e:
                    logger.exception("입력 처리 오류")
                    try:
                        channel.send(json.dumps({
                            "type": "error",
                            "message": f"입력 처리 오류: {str(e)}"
                        }))
                    except Exception:
                        pass

        # 공용 화면 소스를 relay로 구독
        video_track = app["relay"].subscribe(app["screen_source"])
        pc.addTrack(video_track)

        # Offer -> Answer
        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)

        logger.info("Peer %s answer 생성 완료 / control_granted=%s", pc_id, control_granted)

        return web.json_response({
            "sdp": pc.localDescription.sdp,
            "type": pc.localDescription.type,
            "control_granted": control_granted,
        })

    async def on_shutdown(app):
        logger.info("서버 종료 중... 모든 PeerConnection 정리")
        tasks = []

        # pc_meta 복사본 기준으로 정리
        for pc_id, meta in list(app["pc_meta"].items()):
            pc = meta.get("pc")
            if pc:
                tasks.append(pc.close())

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        app["pcs"].clear()
        app["pc_meta"].clear()
        app["controller_id"] = None

    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_post("/offer", offer)
    app.on_shutdown.append(on_shutdown)

    return app


async def cleanup_peer(app, pc_id: str):
    """
    연결 종료/실패 시 peer 정리
    """
    meta = app["pc_meta"].get(pc_id)
    if not meta:
        return

    pc = meta.get("pc")
    is_controller = meta.get("is_controller", False)

    try:
        if pc:
            await pc.close()
    except Exception:
        pass

    try:
        if pc in app["pcs"]:
            app["pcs"].remove(pc)
    except Exception:
        pass

    # 제어권자였다면 제어권 해제
    if is_controller and app["controller_id"] == pc_id:
        app["controller_id"] = None
        logger.info("제어권 해제됨: %s", pc_id)

    app["pc_meta"].pop(pc_id, None)
    logger.info("Peer 정리 완료: %s / 현재 접속자 수=%d", pc_id, len(app["pcs"]))


# =========================
# 메인
# =========================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WebRTC 화면 공유 + 입력 제어 (최대 2명)")
    parser.add_argument("--host", default="0.0.0.0", help="서버 바인딩 주소")
    parser.add_argument("--port", type=int, default=8080, help="HTTP 포트")
    parser.add_argument("--monitor", type=int, default=1, help="공유할 모니터 번호 (기본 1)")
    parser.add_argument("--fps", type=int, default=15, help="전송 FPS (기본 15)")
    parser.add_argument("--scale", type=int, default=2, help="해상도 축소 비율 (기본 2)")
    parser.add_argument("--access-token", required=True, help="화면 보기 토큰")
    parser.add_argument("--control-token", required=True, help="입력 제어 토큰")
    args = parser.parse_args()

    logger.info(
        "서버 시작 host=%s port=%s monitor=%s fps=%s scale=%s",
        args.host, args.port, args.monitor, args.fps, args.scale
    )

    app = create_app(args)
    web.run_app(app, host=args.host, port=args.port)