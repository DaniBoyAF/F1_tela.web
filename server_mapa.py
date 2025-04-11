import socket
import struct
import threading
from flask import Flask, jsonify, render_template, request
import os

UDP_IP = "0.0.0.0"
UDP_PORT = 20777
        
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))

# Dados de posições e nomes
posicoes = {i: {"worldPositionX": 0, "worldPositionZ": 0, "nome": f"Carro {i}", "equipe": f"Equipe {i % 10}"} for i in range(22)}
# Dados de pneus
dados_pneus = {i: {"FL": {"desgaste": 0, "tipo": "N/A"}, "FR": {"desgaste": 0, "tipo": "N/A"}, 
                   "RL": {"desgaste": 0, "tipo": "N/A"}, "RR": {"desgaste": 0, "tipo": "N/A"}} for i in range(22)}
# Dados da tabela de tempos
dados_tempos = {i: {"carNumber": i, "nome": f"Carro {i}", "gapLider": 0, "gapFrente": 0, "s1": 0, "s2": 0, "s3": 0, 
                    "lapTime": 0, "tyre": "N/A", "pitStops": 0, "status": "RUN", "currentLap": 0, "tyreWear": 0} for i in range(22)}
# Dados de telemetria do piloto (foco no jogador)
telemetria = {"speed": 0, "rpm": 0, "gear": 0, "drs": 0, "throttle": 0, "brake": 0, "flag": "NONE", "currentLap": 0, "totalLaps": 0}
track_info = {"trackId": -1, "minX": -1000, "maxX": 1000, "minZ": -1000, "maxZ": 1000}

# Dados de telemetria ao longo da volta para cada piloto
telemetria_volta = {i: {"lapDistance": [], "speed": [], "throttle": [], "brake": [], "gear": [], "rpm": [], "gapLider": [], "currentLap": 0} for i in range(22)}

track_limits = {
    0: {"minX": -500, "maxX": 500, "minZ": -600, "maxZ": 600},  # Melbourne
    1: {"minX": -700, "maxX": 700, "minZ": -800, "maxZ": 800},  # Paul Ricard
    -1: {"minX": -1000, "maxX": 1000, "minZ": -1000, "maxZ": 1000},
}

# Coordenadas simplificadas do traçado (exemplo para Melbourne)
track_paths = {
    0: [  # Melbourne (coordenadas X, Z simplificadas)
        (-400, 500), (-300, 550), (-200, 500), (-100, 400), (0, 300),
        (100, 200), (200, 100), (300, 0), (400, -100), (450, -200),
        (400, -300), (300, -400), (200, -450), (100, -500), (0, -550),
        (-100, -500), (-200, -400), (-300, -300), (-400, -200), (-450, 0),
        (-400, 200), (-400, 400)
    ],
    -1: []  # Padrão: sem traçado
}

compound_map = {16: "S", 17: "M", 18: "H", 7: "I", 8: "W"}
flag_map = {-1: "NONE", 0: "NONE", 1: "GREEN", 2: "BLUE", 3: "YELLOW", 4: "RED"}

app = Flask(__name__)

def processar_motion_data(data):
    motion_data_offset = 24
    car_data_size = 60
    for i in range(22):
        base = motion_data_offset + i * car_data_size
        if len(data) < base + 12:
            continue
        world_position_x = struct.unpack_from("<f", data, base + 0)[0]
        world_position_z = struct.unpack_from("<f", data, base + 8)[0]
        posicoes[i]["worldPositionX"] = world_position_x
        posicoes[i]["worldPositionZ"] = world_position_z

def processar_session_data(data):
    session_data_offset = 24
    if len(data) < session_data_offset + 3:
        return
    track_id = struct.unpack_from("b", data, session_data_offset + 0)[0]
    flag = struct.unpack_from("b", data, session_data_offset + 1)[0]
    total_laps = struct.unpack_from("B", data, session_data_offset + 2)[0]  # Total de voltas
    track_info["trackId"] = track_id
    limits = track_limits.get(track_id, track_limits[-1])
    track_info.update(limits)
    telemetria["flag"] = flag_map.get(flag, "NONE")
    telemetria["totalLaps"] = total_laps

def processar_participants_data(data):
    participants_offset = 24
    num_cars = struct.unpack_from("B", data, participants_offset)[0]
    for i in range(min(num_cars, 22)):
        base = participants_offset + 1 + i * 56
        if len(data) < base + 48:
            continue
        nome = data[base + 19:base + 39].decode("utf-8", errors="ignore").strip("\x00")
        car_number = struct.unpack_from("B", data, base + 2)[0]
        posicoes[i]["nome"] = nome if nome else f"Carro {i}"
        dados_tempos[i]["nome"] = nome if nome else f"Carro {i}"
        dados_tempos[i]["carNumber"] = car_number

def processar_car_status_data(data):
    car_status_offset = 24
    car_data_size = 60
    for i in range(22):
        base = car_status_offset + i * car_data_size
        if len(data) < base + 28:
            continue
        compound = struct.unpack_from("B", data, base + 23)[0]
        tire_type = compound_map.get(compound, "N/A")
        fl_wear = struct.unpack_from("B", data, base + 24)[0]
        fr_wear = struct.unpack_from("B", data, base + 25)[0]
        rl_wear = struct.unpack_from("B", data, base + 26)[0]
        rr_wear = struct.unpack_from("B", data, base + 27)[0]
        pit_stops = struct.unpack_from("B", data, base + 22)[0]
        avg_wear = (fl_wear + fr_wear + rl_wear + rr_wear) / 4
        dados_pneus[i] = {
            "FL": {"desgaste": fl_wear, "tipo": tire_type},
            "FR": {"desgaste": fr_wear, "tipo": tire_type},
            "RL": {"desgaste": rl_wear, "tipo": tire_type},
            "RR": {"desgaste": rr_wear, "tipo": tire_type},
        }
        dados_tempos[i]["tyre"] = tire_type
        dados_tempos[i]["pitStops"] = pit_stops
        dados_tempos[i]["tyreWear"] = avg_wear

def processar_lap_data(data):
    lap_data_offset = 24
    car_data_size = 53
    player_car_idx = struct.unpack_from("B", data, 21)[0]  # Índice do jogador
    for i in range(22):
        base = lap_data_offset + i * car_data_size
        if len(data) < base + 24:
            continue
        last_lap_time = struct.unpack_from("<f", data, base + 0)[0]
        sector1_time = struct.unpack_from("<f", data, base + 12)[0]
        sector2_time = struct.unpack_from("<f", data, base + 16)[0]
        total_distance = struct.unpack_from("<f", data, base + 8)[0]
        pit_status = struct.unpack_from("B", data, base + 26)[0]
        current_lap = struct.unpack_from("B", data, base + 4)[0]  # Volta atual
        lap_distance = struct.unpack_from("<f", data, base + 20)[0]  # Distância na volta atual
        
        dados_tempos[i]["lapTime"] = last_lap_time if last_lap_time > 0 else dados_tempos[i]["lapTime"]
        dados_tempos[i]["s1"] = sector1_time if sector1_time > 0 else dados_tempos[i]["s1"]
        dados_tempos[i]["s2"] = sector2_time if sector2_time > 0 else dados_tempos[i]["s2"]
        if dados_tempos[i]["lapTime"] > 0 and dados_tempos[i]["s1"] > 0 and dados_tempos[i]["s2"] > 0:
            dados_tempos[i]["s3"] = dados_tempos[i]["lapTime"] - (dados_tempos[i]["s1"] + dados_tempos[i]["s2"])
        dados_tempos[i]["totalDistance"] = total_distance
        dados_tempos[i]["status"] = "PIT" if pit_status == 1 else "RUN"
        dados_tempos[i]["currentLap"] = current_lap
        
        # Atualiza a volta atual do piloto principal
        if i == player_car_idx:
            telemetria["currentLap"] = current_lap

        # Salva os dados de telemetria ao longo da volta
        if telemetria_volta[i]["currentLap"] != current_lap:
            # Nova volta, limpa os dados anteriores
            telemetria_volta[i] = {
                "lapDistance": [],
                "speed": [],
                "throttle": [],
                "brake": [],
                "gear": [],
                "rpm": [],
                "gapLider": [],
                "currentLap": current_lap
            }
        telemetria_volta[i]["lapDistance"].append(lap_distance)
        telemetria_volta[i]["gapLider"].append(dados_tempos[i]["gapLider"])

    sorted_cars = sorted(dados_tempos.items(), key=lambda x: x[1]["totalDistance"], reverse=True)
    leader_distance = sorted_cars[0][1]["totalDistance"]
    for idx, (car_id, car_data) in enumerate(sorted_cars):
        distance_diff = leader_distance - car_data["totalDistance"]
        car_data["gapLider"] = distance_diff / 50.0
        if idx == 0:
            car_data["gapFrente"] = 0
        else:
            prev_distance = sorted_cars[idx - 1][1]["totalDistance"]
            car_data["gapFrente"] = (prev_distance - car_data["totalDistance"]) / 50.0
        car_data["position"] = idx + 1

def processar_telemetry_data(data):
    telemetry_offset = 24
    car_data_size = 60
    player_car_idx = struct.unpack_from("B", data, 21)[0]  # Índice do jogador
    for i in range(22):
        base = telemetry_offset + i * car_data_size
        if len(data) < base + 16:
            continue
        speed = struct.unpack_from("<H", data, base + 0)[0]  # km/h
        throttle = struct.unpack_from("<f", data, base + 2)[0]
        brake = struct.unpack_from("<f", data, base + 4)[0]
        lateral_acceleration = struct.unpack_from("<f", data, base + 12)[0]  # Aceleração lateral
        fuel = struct.unpack_from("<f", data, base + 14)[0]  # Consumo de combustível

        if i == player_car_idx:
            telemetria.update({
                "speed": speed,
                "throttle": throttle * 100,
                "brake": brake * 100,
                "lateralAcceleration": lateral_acceleration,
                "fuel": fuel
            })

def coletar_dados_udp():
    while True:
        data, _ = sock.recvfrom(2048)
        if len(data) < 6:
            continue
        packet_id = struct.unpack_from("B", data, 5)[0]
        if packet_id == 0:  # Motion Data
            processar_motion_data(data)
        elif packet_id == 1:  # Session Data
            processar_session_data(data)
        elif packet_id == 2:  # Lap Data
            processar_lap_data(data)
        elif packet_id == 4:  # Participants Data
            processar_participants_data(data)
        elif packet_id == 6:  # Car Telemetry Data
            processar_telemetry_data(data)
        elif packet_id == 7:  # Car Status Data
            processar_car_status_data(data)

# Variável global para o modo atual
modo_atual = "race"  # Pode ser "race" ou "qualy"

@app.route("/api/modo", methods=["GET", "POST"])
def alterar_modo():
    global modo_atual
    if request.method == "POST":
        novo_modo = request.json.get("modo")
        if novo_modo in ["race", "qualy"]:
            modo_atual = novo_modo
            return jsonify({"status": "success", "modo": modo_atual})
        return jsonify({"status": "error", "message": "Modo inválido"}), 400
    return jsonify({"modo": modo_atual})

@app.route("/api/posicoes")
def get_posicoes():
    return jsonify({
        "posicoes": list(posicoes.values()),
        "track": track_info,
        "path": track_paths.get(track_info["trackId"], [])
    })

@app.route("/api/pneus")
def get_pneus():
    return jsonify(dados_pneus)

@app.route("/api/tempos")
def get_tempos():
    if modo_atual == "race":
        # Ordenar pela distância total percorrida
        sorted_cars = sorted(dados_tempos.items(), key=lambda x: x[1]["totalDistance"], reverse=True)
    elif modo_atual == "qualy":
        # Ordenar pelo melhor tempo de volta
        sorted_cars = sorted(dados_tempos.items(), key=lambda x: x[1]["lapTime"] if x[1]["lapTime"] > 0 else float("inf"))
    return jsonify([car_data for _, car_data in sorted_cars])

@app.route("/api/telemetria")
def get_telemetria():
    return jsonify({
        "speed": telemetria["speed"],
        "throttle": telemetria["throttle"],
        "brake": telemetria["brake"],
        "lateralAcceleration": telemetria.get("lateralAcceleration", 0),
        "fuel": telemetria.get("fuel", 0),
        "gear": telemetria.get("gear", 0),
        "rpm": telemetria.get("rpm", 0),
        "drs": telemetria.get("drs", 0),
        "flag": telemetria.get("flag", "NONE"),
        "currentLap": telemetria.get("currentLap", 0),
        "totalLaps": telemetria.get("totalLaps", 0)
    })

@app.route("/api/telemetria_volta/<int:piloto1>/<int:piloto2>")
def get_telemetria_volta(piloto1, piloto2):
    return jsonify({
        "piloto1": telemetria_volta[piloto1],
        "piloto2": telemetria_volta[piloto2],
        "nomes": {
            "piloto1": dados_tempos[piloto1]["nome"],
            "piloto2": dados_tempos[piloto2]["nome"]
        }
    })

@app.route("/")
def home():
    return render_template("index.html")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
