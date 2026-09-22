# UART Robot Full Run Flow

This project now uses Python for AI/web logic and UART for vehicle control.

## 1. Hardware Setup

1. Connect the motor driver to your Arduino/ESP32.
2. Open `firmware/uart_robot_controller/uart_robot_controller.ino`.
3. Update the motor pins at the top of the file to match your wiring.
4. Upload the firmware with Arduino IDE.
5. Keep the board connected by USB.

The firmware uses this UART protocol:

```text
M:FORWARD:80
M:BACKWARD:80
M:LEFT:65
M:RIGHT:65
M:STOP
A:PICK
A:ARRIVE
```

Safety behavior: if the board receives no command for 500 ms, it stops the motors.

## 2. Find The UART Port

On Windows, open Device Manager and check:

```text
Ports (COM & LPT)
```

Common examples:

```text
COM3
COM10
```

If no COM port appears, reconnect the board, try another USB cable, or install the board driver.

## 3. Configure `.env`

Add these values to `.env` and change `UART_PORT` to your board port:

```env
UART_ENABLED=1
UART_DRY_RUN=0
UART_PORT=COM10
UART_BAUD=115200
UART_DEFAULT_SPEED=80
UART_TURN_SPEED=65
```

For testing without the robot, use:

```env
UART_DRY_RUN=1
```

## 4. Test Python UART Encoding

Run this from the project folder:

```powershell
.venv\Scripts\python.exe -c "from uart_controller import UartRobotController; c=UartRobotController(dry_run=True); print(c.send('forward')); print(c.send('left')); print(c.send('stop'))"
```

Expected output:

```text
M:FORWARD:80
M:LEFT:65
M:STOP
```

## 5. Test Real UART Command

After setting the real `UART_PORT`, run:

```powershell
.venv\Scripts\python.exe -c "from dotenv import load_dotenv; load_dotenv(); from uart_controller import create_uart_controller; c=create_uart_controller(); print(c.send('forward')); import time; time.sleep(0.2); print(c.send('stop')); c.close()"
```

The robot should move forward briefly, then stop.

## 6. Start The Full System

Run:

```powershell
.venv\Scripts\python.exe app.py
```

Open:

```text
http://localhost:5000
```

The startup flow is:

```text
app.py
  -> imports s.py
  -> s.start_robot_system()
  -> FrameReader reads RTSP camera
  -> InferenceWorker runs YOLO / Depth / MediaPipe
  -> StateMachineController decides movement
  -> uart_controller sends UART packets
  -> Arduino/ESP32 firmware drives motors
```

## 7. Manual API Test

With `app.py` running:

```powershell
Invoke-RestMethod -Uri http://localhost:5000/control -Method Post -ContentType "application/json" -Body '{"command":"forward"}'
Start-Sleep -Milliseconds 200
Invoke-RestMethod -Uri http://localhost:5000/control -Method Post -ContentType "application/json" -Body '{"command":"stop"}'
```

## 8. Auto Mode

In the web page, switch the mode to Auto.

The AI loop will send commands such as:

```text
forward
left
right
pick
arrive
```

Python converts them to UART packets before sending them to the vehicle.

## 9. Common Problems

`UART dry-run` appears in logs:

```text
UART_PORT is missing, UART_DRY_RUN=1, or the port could not be opened.
```

Robot does not move:

```text
Check motor driver power, GND sharing, motor pin mapping, and UART_PORT.
```

Robot moves in the wrong direction:

```text
Swap motor wires or reverse direction logic in the firmware.
```

App starts but no camera frame:

```text
Check STREAM_URL in .env and confirm the RTSP stream is running.
```
