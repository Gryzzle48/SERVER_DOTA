import socket
import threading
import time
from collections import defaultdict

class EnhancedSyncServer:
    def __init__(self, host='0.0.0.0', port=1234, min_clients=2):
        self.host = host
        self.port = port
        self.sessions = defaultdict(dict)
        self.connections = {}
        self.lock = threading.Lock()
        self.running = True
        self.min_clients = min_clients
        self.game_timers = {}

    def handle_client(self, conn, addr):
        client_id = None
        session_id = None
        
        try:
            while self.running:
                try:
                    data = conn.recv(1024).decode().strip()
                    if not data:
                        break
                except ConnectionResetError:
                    print(f"Соединение с клиентом {addr} разорвано")
                    break
                    
                parts = data.split(':')
                if len(parts) < 3:
                    continue
                    
                command, session, cid = parts[0], parts[1], parts[2]
                
                with self.lock:
                    if command == "REGISTER":
                        self._handle_register(conn, session, cid)
                        client_id = cid
                        session_id = session
                        
                    elif command == "READY":
                        self._handle_ready(session, cid)
                        
                    elif command == "FOUND":
                        self._handle_found(session, cid)
                        
                    elif command == "RECONNECT":
                        self._handle_reconnect(conn, session, cid)
                        
                    elif command == "PONG":
                        if session in self.sessions and cid in self.sessions[session]:
                            self.sessions[session][cid]['last_active'] = time.time()
                    
            # Обработка таймера игры
            if session_id and client_id:
                self._check_game_timer(session_id)  # Исправлено: _check_game_timer вместо _check_game_time
                
        except Exception as e:
            print(f"Ошибка обработки клиента: {e}")
        finally:
            self._cleanup_client(client_id, session_id, conn)

    def _handle_register(self, conn, session, cid):
        if cid in self.connections:
            try:
                self.connections[cid].close()
            except:
                pass
                
        self.sessions[session][cid] = {
            'ready': False,
            'found': False,
            'active': True,
            'last_active': time.time(),
            'conn': conn
        }
        self.connections[cid] = conn
        conn.sendall(b"ACK:REGISTER")
        print(f"Клиент {cid} зарегистрирован в сессии {session}")

    def _handle_ready(self, session, cid):
        if session in self.sessions and cid in self.sessions[session]:
            self.sessions[session][cid]['ready'] = True
            self.sessions[session][cid]['last_active'] = time.time()
            
            client_conn = self.sessions[session][cid]['conn']
            try:
                client_conn.sendall(b"ACK:READY")
            except:
                self.sessions[session][cid]['active'] = False
                
            print(f"Клиент {cid} готов к поиску")
            
            if self._check_start_condition(session):
                print(f"Все клиенты готовы, отправляю START для сессии {session}")
                for client_id, client_data in self.sessions[session].items():
                    try:
                        if client_data['active']:
                            client_data['conn'].sendall(b"START")
                    except:
                        self.sessions[session][client_id]['active'] = False

    def _handle_found(self, session, cid):
        if session in self.sessions and cid in self.sessions[session]:
            self.sessions[session][cid]['found'] = True
            self.sessions[session][cid]['last_active'] = time.time()
            print(f"Клиент {cid} нашел игру")
            
            if session not in self.game_timers:
                self.game_timers[session] = {
                    'timer': threading.Timer(5.0, self._handle_game_timeout, [session]),
                    'found_clients': set()
                }
                self.game_timers[session]['timer'].start()
                
            self.game_timers[session]['found_clients'].add(cid)
            self._check_game_confirmation(session)

    def _handle_reconnect(self, conn, session, cid):
        if session in self.sessions and cid in self.sessions[session]:
            self.sessions[session][cid]['conn'] = conn
            self.connections[cid] = conn
            self.sessions[session][cid]['active'] = True
            self.sessions[session][cid]['last_active'] = time.time()
            try:
                conn.sendall(b"ACK:RECONNECT")
            except:
                self.sessions[session][cid]['active'] = False
            print(f"Клиент {cid} переподключен")
            
            if self.sessions[session][cid]['found']:
                try:
                    conn.sendall(b"FOUND")
                except:
                    self.sessions[session][cid]['active'] = False
            elif self.sessions[session][cid]['ready']:
                try:
                    conn.sendall(b"READY")
                except:
                    self.sessions[session][cid]['active'] = False

    def _check_game_confirmation(self, session):
        if session not in self.game_timers:
            return
            
        timer_data = self.game_timers[session]
        session_data = self.sessions.get(session, {})
        
        if len(timer_data['found_clients']) == sum(1 for c in session_data.values() if c['active']):
            print(f"Все активные клиенты нашли игру, подтверждаем")
            for client in session_data.values():
                if client['active']:
                    try:
                        client['conn'].sendall(b"ACCEPT")
                    except:
                        client['active'] = False
            self._cleanup_game_timer(session)

    def _handle_game_timeout(self, session):
        with self.lock:
            if session not in self.game_timers:
                return
                
            timer_data = self.game_timers[session]
            session_data = self.sessions.get(session, {})
            
            print(f"Таймаут подтверждения игры для сессии {session}")
            for cid, client in session_data.items():
                if client['active']:
                    try:
                        client['conn'].sendall(b"SKIP")
                    except:
                        client['active'] = False
                    
            self._cleanup_game_timer(session)

    def _cleanup_game_timer(self, session):
        if session in self.game_timers:
            try:
                self.game_timers[session]['timer'].cancel()
            except:
                pass
            del self.game_timers[session]

    def _check_start_condition(self, session):
        if session not in self.sessions:
            return False
            
        session_data = self.sessions[session]
        active_clients = sum(1 for c in session_data.values() if c['active'])
        ready_clients = sum(1 for c in session_data.values() if c['ready'] and c['active'])
        return ready_clients >= self.min_clients and ready_clients == active_clients

    def _check_game_timer(self, session):
        """Проверяет и обрабатывает таймеры игры для сессии"""
        if session in self.game_timers:
            timer_data = self.game_timers[session]
            if not timer_data['timer'].is_alive():
                self._handle_game_timeout(session)

    def _cleanup_client(self, cid, session_id, conn):
        with self.lock:
            if cid and session_id and session_id in self.sessions and cid in self.sessions[session_id]:
                del self.sessions[session_id][cid]
                print(f"Клиент {cid} удален из сессии {session_id}")
                
            if cid in self.connections:
                del self.connections[cid]
                
            try:
                conn.close()
            except:
                pass

    def start(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.host, self.port))
            s.listen(10)
            print(f"Сервер запущен на {self.host}:{self.port}")
            
            while self.running:
                try:
                    conn, addr = s.accept()
                    threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()
                except Exception as e:
                    if self.running:
                        print(f"Ошибка подключения: {e}")

    def stop(self):
        self.running = False
        with self.lock:
            for conn in self.connections.values():
                try:
                    conn.close()
                except:
                    pass
            self.connections.clear()

if __name__ == "__main__":
    server = EnhancedSyncServer(min_clients=2)
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()
        print("\nСервер остановлен")
