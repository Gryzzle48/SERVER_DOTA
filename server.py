import socket
import threading
import time
from collections import defaultdict
import queue
import datetime

class SyncServer:
    def __init__(self, host='0.0.0.0', port=1234):
        self.host = host
        self.port = port
        self.sessions = defaultdict(dict)
        self.connections = {}
        self.client_sessions = {}
        self.lock = threading.Lock()
        self.running = True
        self.session_timeout = 600  # 10 минут
        self.connection_timeout = 300  # 5 минут
        self.match_events = {}
        self.pending_matches = queue.Queue()
        self.start_threshold = 2  # Минимальное количество клиентов для старта
        
    def log(self, message):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] {message}")
        
    def broadcast(self, session_id, message):
        with self.lock:
            if session_id not in self.sessions:
                return
                
            for client_id in self.sessions[session_id]:
                if client_id in self.connections:
                    try:
                        self.connections[client_id].sendall((message + '\n').encode())
                        self.log(f"Отправлено '{message}' клиенту {client_id}")
                    except Exception as e:
                        self.log(f"Ошибка отправки клиенту {client_id}: {e}")

    def handle_client(self, conn, addr):
        self.log(f"Подключен клиент: {addr}")
        client_id = None
        session_id = None
        
        try:
            conn.settimeout(self.connection_timeout)
            
            while self.running:
                try:
                    data = conn.recv(4096).decode()
                    if not data:
                        self.log(f"Клиент {addr} отправил пустые данные")
                        continue
                        
                    for command in data.split('\n'):
                        if not command:
                            continue
                        self.process_command(conn, addr, command)
                        
                except socket.timeout:
                    continue
                except ConnectionResetError:
                    self.log(f"Клиент {addr} принудительно разорвал соединение")
                    break
                except Exception as e:
                    self.log(f"Ошибка чтения от клиента {addr}: {e}")
                    break
                    
        except Exception as e:
            self.log(f"Критическая ошибка обработки клиента {addr}: {e}")
        finally:
            with self.lock:
                if client_id:
                    if client_id in self.connections:
                        try:
                            del self.connections[client_id]
                        except:
                            pass
                    
                    if client_id in self.client_sessions:
                        session_id = self.client_sessions[client_id]
                        if session_id in self.sessions and client_id in self.sessions[session_id]:
                            del self.sessions[session_id][client_id]
                            self.log(f"Клиент {client_id} удален из сессии {session_id}")
                        
                            if not self.sessions[session_id]:
                                del self.sessions[session_id]
                                self.log(f"Сессия {session_id} удалена")
                        
                        del self.client_sessions[client_id]
            
            try:
                conn.close()
            except:
                pass
            self.log(f"Клиент {addr} отключен")

    def process_command(self, conn, addr, data):
        parts = data.split(':')
        if len(parts) < 2:
            self.log(f"Некорректный формат команды от {addr}: {data}")
            return
            
        command = parts[0]
        session_id = parts[1]
        
        with self.lock:
            if command == "REGISTER":
                if len(parts) < 3:
                    self.log(f"Некорректная команда REGISTER от {addr}")
                    return
                client_id = parts[2]
                
                if client_id in self.client_sessions:
                    old_session = self.client_sessions[client_id]
                    if client_id in self.sessions[old_session]:
                        del self.sessions[old_session][client_id]
                        self.log(f"Удалена старая регистрация клиента {client_id} из сессии {old_session}")
                
                self.sessions[session_id][client_id] = {
                    'ready': False, 
                    'last_active': time.time(),
                    'match_found': False,
                    'address': addr
                }
                self.connections[client_id] = conn
                self.client_sessions[client_id] = session_id
                
                session_info = self.get_session_info(session_id)
                self.log(f"Клиент {client_id} зарегистрирован в сессии {session_id}")
                self.log(f"Состояние сессии {session_id}: {session_info}")
                
                conn.sendall(b"ACK:REGISTER\n")
                
            elif command == "READY":
                if len(parts) < 3:
                    return
                client_id = parts[2]
                
                if session_id not in self.sessions:
                    self.log(f"Сессия {session_id} не найдена для READY")
                    return
                if client_id not in self.sessions[session_id]:
                    self.log(f"Клиент {client_id} не найден в сессии {session_id}")
                    return
                    
                self.sessions[session_id][client_id]['ready'] = True
                self.sessions[session_id][client_id]['last_active'] = time.time()
                self.log(f"Клиент {client_id} готов в сессии {session_id}")
                conn.sendall(b"ACK:READY\n")
                
                session = self.sessions[session_id]
                total_clients = len(session)
                ready_clients = sum(1 for c in session.values() if c['ready'])
                
                self.log(f"Готовность в сессии {session_id}: {ready_clients}/{total_clients}")
                
                if ready_clients >= self.start_threshold:
                    self.log(f"Достигнут порог готовности ({ready_clients}/{total_clients})! Отправляю START для сессии {session_id}")
                    for cid in session:
                        if cid in self.connections and session[cid]['ready']:
                            try:
                                self.connections[cid].sendall(b"START\n")
                                session[cid]['ready'] = False
                                self.log(f"Отправлен START клиенту {cid}")
                            except Exception as e:
                                self.log(f"Ошибка отправки START клиенту {cid}: {e}")
            
            elif command == "MATCH_FOUND":
                if len(parts) < 3:
                    return
                client_id = parts[2]
                
                if session_id not in self.sessions:
                    self.log(f"Сессия {session_id} не найдена для MATCH_FOUND")
                    return
                if client_id not in self.sessions[session_id]:
                    self.log(f"Клиент {client_id} не найден в сессии {session_id}")
                    return
                    
                self.sessions[session_id][client_id]['match_found'] = True
                self.log(f"Клиент {client_id} нашел игру в сессии {session_id}")
                conn.sendall(b"ACK:MATCH_FOUND\n")
                
                self.pending_matches.put((session_id, client_id))
            
            elif command == "PING":
                if len(parts) < 3:
                    return
                client_id = parts[2]
                
                if session_id in self.sessions and client_id in self.sessions[session_id]:
                    self.sessions[session_id][client_id]['last_active'] = time.time()
                    conn.sendall(b"PONG\n")
                    self.log(f"Получен PING от {client_id}, отправлен PONG")
                else:
                    self.log(f"PING от неизвестного клиента {client_id} в сессии {session_id}")

    def get_session_info(self, session_id):
        if session_id not in self.sessions:
            return "Сессия не найдена"
            
        session = self.sessions[session_id]
        info = []
        for client_id, data in session.items():
            status = "готов" if data['ready'] else "не готов"
            last_active = time.strftime("%H:%M:%S", time.localtime(data['last_active']))
            info.append(f"{client_id} ({status}, активен: {last_active})")
        
        return f"Всего клиентов: {len(session)} [{' | '.join(info)}]"

    def handle_match_acceptance(self):
        while self.running:
            try:
                session_id, client_id = self.pending_matches.get(timeout=1)
                
                with self.lock:
                    active_clients = []
                    for cid, data in self.sessions[session_id].items():
                        if time.time() - data['last_active'] < self.session_timeout:
                            active_clients.append(cid)
                    
                    if len(active_clients) < 2:
                        self.log(f"В сессии {session_id} недостаточно активных клиентов ({len(active_clients)}/2)")
                        continue
                    
                    other_client = None
                    for cid in active_clients:
                        if cid != client_id:
                            other_client = cid
                            break
                    
                    if not other_client:
                        self.log(f"В сессии {session_id} только один активный клиент")
                        self.broadcast(session_id, "SKIP")
                        continue
                    
                    second_found = False
                    for _ in range(5):
                        if other_client in self.sessions[session_id] and \
                           self.sessions[session_id][other_client].get('match_found', False):
                            second_found = True
                            break
                        time.sleep(1)
                    
                    if second_found:
                        self.log(f"Обе стороны нашли игру, разрешаем прием для сессии {session_id}")
                        self.broadcast(session_id, "ACCEPT")
                    else:
                        self.log(f"Вторая сторона не нашла игру, пропускаем для сессии {session_id}")
                        self.broadcast(session_id, "SKIP")
                        if other_client in self.sessions[session_id]:
                            self.sessions[session_id][other_client]['match_found'] = False
                        
            except queue.Empty:
                continue
            except Exception as e:
                self.log(f"Ошибка обработки матча: {e}")

    def clean_inactive_sessions(self):
        while self.running:
            time.sleep(30)
            with self.lock:
                now = time.time()
                inactive_clients = []
                
                for session_id, session in self.sessions.items():
                    for client_id, client_data in session.items():
                        if now - client_data['last_active'] > self.session_timeout:
                            inactive_clients.append((session_id, client_id))
                
                for session_id, client_id in inactive_clients:
                    self.log(f"Удаление неактивного клиента {client_id} из сессии {session_id}")
                    
                    if client_id in self.connections:
                        try:
                            self.connections[client_id].close()
                        except:
                            pass
                        del self.connections[client_id]
                    
                    if session_id in self.sessions and client_id in self.sessions[session_id]:
                        del self.sessions[session_id][client_id]
                    
                    if client_id in self.client_sessions:
                        del self.client_sessions[client_id]
                
                empty_sessions = []
                for session_id, session in self.sessions.items():
                    if not session:
                        empty_sessions.append(session_id)
                
                for session_id in empty_sessions:
                    del self.sessions[session_id]
                    self.log(f"Сессия {session_id} удалена по таймауту")
                    
                self.log("===== ТЕКУЩЕЕ СОСТОЯНИЕ СЕРВЕРА =====")
                for session_id in list(self.sessions.keys()):
                    self.log(f"Сессия {session_id}: {self.get_session_info(session_id)}")
                self.log("=====================================")

    def start(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.host, self.port))
            s.listen(10)
            self.log(f"Сервер запущен на {self.host}:{self.port}")
            
            threading.Thread(target=self.clean_inactive_sessions, daemon=True).start()
            threading.Thread(target=self.handle_match_acceptance, daemon=True).start()
            
            while self.running:
                try:
                    conn, addr = s.accept()
                    threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()
                except Exception as e:
                    if self.running:
                        self.log(f"Ошибка подключения: {e}")

    def stop(self):
        self.running = False
        with self.lock:
            for conn in self.connections.values():
                try:
                    conn.close()
                except:
                    pass
            self.connections.clear()
        self.log("Сервер остановлен")

if __name__ == "__main__":
    server = SyncServer()
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()
        print("\nСервер остановлен")
