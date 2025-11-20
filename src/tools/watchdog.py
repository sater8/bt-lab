import time
import requests
import os

HEARTBEAT_FILE = "bot_heartbeat.txt"

# Webhook de ALERTA (NO el mismo del bot de señales)
WEBHOOK_ALERT = "https://discord.com/api/webhooks/1441122074587299881/UzzUqU2-mAgGVW8Gmx92tAvBTlSkp6O9ez2e5yZCOKhIIPnHZvWZavE9ANzOn28hRkrT"

CHECK_INTERVAL = 120   # cada 2 minutos comprobamos el latido
TIMEOUT = 600          # si pasan > 600s (10min) sin latido → alerta


def send_alert(msg: str):
    try:
        requests.post(WEBHOOK_ALERT, json={"content": msg}, timeout=10)
    except Exception as e:
        print(f"[WATCHDOG ERROR ENVIANDO ALERTA] {e}")


def watchdog():
    print("🐶 Watchdog iniciado... vigilando el heartbeat del bot")

    while True:
        try:
            if not os.path.exists(HEARTBEAT_FILE):
                send_alert("⚠️ **Bot offline:** no existe `bot_heartbeat.txt` (¿bot no iniciado?).")
            else:
                with open(HEARTBEAT_FILE, "r") as f:
                    raw = f.read().strip()

                if not raw:
                    send_alert("⚠️ **Bot offline:** `bot_heartbeat.txt` está vacío.")
                else:
                    last = float(raw)
                    now = time.time()
                    diff = now - last

                    if diff > TIMEOUT:
                        send_alert(
                            f"🚨 **ALERTA:** El bot lleva `{int(diff)}s` sin latir.\n"
                            "Probablemente está caído o sin conexión."
                        )
        except Exception as e:
            print(f"[WATCHDOG ERROR] {e}")
            # Opcional: mandar también esto al Discord
            # send_alert(f"⚠️ *Watchdog error:* {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    watchdog()
