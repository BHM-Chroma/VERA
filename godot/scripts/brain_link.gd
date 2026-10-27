class_name BrainLink
extends Node
## Связь с Python-«мозгом» по WebSocket (ws://127.0.0.1:8765).
## Если мозг не запущен — тихо переподключается раз в несколько секунд.

signal message_received(msg: Dictionary)
signal connection_changed(connected: bool)

@export var url := "ws://127.0.0.1:8765"
@export var reconnect_delay := 3.0

var connected := false
var _ws := WebSocketPeer.new()
var _retry := 0.0
var _connecting := false
var _next_id := 1
var _replies := {}


func _ready() -> void:
	_connect()


func _connect() -> void:
	_ws = WebSocketPeer.new()
	_ws.inbound_buffer_size = 1 << 20
	var err := _ws.connect_to_url(url)
	_connecting = err == OK
	if err != OK:
		_retry = reconnect_delay


func _process(delta: float) -> void:
	if not _connecting:
		_retry -= delta
		if _retry <= 0.0:
			_connect()
		return

	_ws.poll()
	var state := _ws.get_ready_state()
	if state == WebSocketPeer.STATE_OPEN and not connected:
		connected = true
		connection_changed.emit(true)
		send({"type": "hello", "client": "godot"})
	# читаем пакеты и при закрытии: последнее сообщение («quit») приходит вместе с ним
	while _ws.get_available_packet_count() > 0:
		var text := _ws.get_packet().get_string_from_utf8()
		var data = JSON.parse_string(text)
		if data is Dictionary:
			if data.has("reply_to"):
				_replies[int(data.reply_to)] = data
			else:
				message_received.emit(data)
	if state == WebSocketPeer.STATE_CLOSED:
		if connected:
			connected = false
			connection_changed.emit(false)
		_connecting = false
		_retry = reconnect_delay


func send(msg: Dictionary) -> void:
	if connected:
		_ws.send_text(JSON.stringify(msg))


## Запрос с ожиданием ответа (например, где сейчас активное окно).
## Возвращает словарь ответа или null, если мозг не ответил вовремя.
func request(msg: Dictionary, timeout := 1.5) -> Variant:
	if not connected:
		return null
	var id := _next_id
	_next_id += 1
	msg["id"] = id
	send(msg)
	var deadline := Time.get_ticks_msec() + int(timeout * 1000.0)
	while Time.get_ticks_msec() < deadline:
		await get_tree().process_frame
		if _replies.has(id):
			var r = _replies[id]
			_replies.erase(id)
			return r
	return null
