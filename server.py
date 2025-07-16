import socket
import threading
import time
import queue
from collections import defaultdict

SERVER_IP = '0.0.0.0'
SERVER_PORT = 1234
SESSION_ID = 'DOTA_BOTS'
SYNC_TIMEOUT = 5
class ClientHandler:
    def __init__(self, conn, addr, server):
        self.conn = conn
        self.addr = addr
        self.server = server
        self.client_id = None
        self.session_id = None
        self.state = "disconnected"
        self.search_status = "idle"
        self.running = True
        self.thread = threading.Thread(target=self.handle_client)
        self.thread.daemon = True
        self.thread.start()
        
    def handle_client(self):
        try:
            while self.running:
                try:
                    data = self.conn.recv(1024).decode().strip()
                    if not data:
                        break
                    
                    parts = data.split(':')
                    cmd = parts[0]
                    if cmd == "REGISTER":
                        if len(parts) < 4:
                            continue
                        self.session_id = parts[1]
                        self.client_id = parts[2]
                        state_parts = parts[3].split(';')
                        self.state = state_parts[0]
                        if len(state_parts) > 1:
                            self.search_status = state_parts[1]
                            
                        self.server.add_client(self)
                        print(f"[{self.client_id}] Зарегистрирован в сессии {self.session_id}")
                        self.conn.sendall(b"OK\n")
                    
                    elif cmd == "READY":
                        if self.session_id and self.client_id:
                            print(f"[{self.client_id}] Отправлен READY")
                            self.state = "ready"
                            self.server.process_ready(self)
                    
                    elif cmd == "FOUND":
                        if self.session_id and self.client_id:
                            print(f"[{self.client_id}] Отправлен FOUND")
                            self.state = "found"
                            self.search_status = "found"
                            self.server.process_found(self)
                    
                    elif cmd == "PING":
                        self.conn.sendall(b"PONG\n")
                    
                    elif cmd == "RECONNECT":
                        if len(parts) >= 4:
                            self.session_id = parts[1]
                            self.client_id = parts[2]
                            state_parts = parts[3].split(';')
                            self.state = state_parts[0]
                            if len(state_parts) > 1:
                                self.search_status = state_parts[1]
                            self.server.add_client(self)
                            print(f"[{self.client_id}] Переподключен")
                            self.conn.sendall(b"OK\n")
                    
                    else:
                        print(f"Неизвестная команда: {data}")
                        self.conn.sendall(b"UNKNOWN\n")
                except socket.timeout:
                    # Убрали отправку PING
                    pass
                except Exception as e:
                    print(f"Ошибка обработки команды: {e}")
        except Exception as e:
            print(f"Ошибка обработки клиента: {e}")
        finally:
            self.running = False
            self.server.remove_client(self)
            try:
                self.conn.close()
            except:
                pass
    
    def send_command(self, command):
        try:
            self.conn.sendall(f"{command}\n".encode())
            return True
        except:
            return False

class SyncServer:
    def __init__(self):
        self.clients = {}
        self.sessions = defaultdict(dict)
        self.lock = threading.Lock()
        self.timers = {}
        
    def add_client(self, client):
        with self.lock:
            self.clients[client.client_id] = client
            if client.session_id == SESSION_ID:
                self.sessions[SESSION_ID][client.client_id] = client
    
    def remove_client(self, client):
        with self.lock:
            if client.client_id in self.clients:
                del self.clients[client.client_id]
            if client.session_id == SESSION_ID and client.client_id in self.sessions[SESSION_ID]:
                del self.sessions[SESSION_ID][client.client_id]
                print(f"Клиент {client.client_id} удален из сессии")
                
                if client.client_id in self.timers:
                    self.timers[client.client_id].cancel()
                    del self.timers[client.client_id]
                
                for other in self.sessions[SESSION_ID].values():
                    if other != client:
                        other.send_command("RESET")
    
    def process_ready(self, client):
        """Улучшенная обработка готовности"""
        with self.lock:
            session_clients = list(self.sessions[SESSION_ID].values())
            ready_clients = [c for c in session_clients if c.state == "ready"]
            
            print(f"Готовых клиентов: {len(ready_clients)}")
            
            if len(ready_clients) >= 2:
                print(f"Достаточно клиентов готово ({len(ready_clients)}), отправляем START")
                for c in ready_clients:
                    c.state = "searching"
                    c.search_status = "searching"
                    c.send_command("START")
    
    def process_found(self, client):
        with self.lock:
            session_clients = list(self.sessions[SESSION_ID].values())
            found_clients = [c for c in session_clients if c.state == "found"]
            
            print(f"Найдено игр: {len(found_clients)}")
            
            if len(found_clients) == 1:
                print(f"[{client.client_id}] Первый нашел игру, запускаем таймер")
                # Сохраняем время обнаружения
                client.found_time = time.time()
                timer = threading.Timer(SYNC_TIMEOUT, self.handle_timeout, [client])
                timer.start()
                self.timers[client.client_id] = timer
                
            elif len(found_clients) >= 2:
                print(f"[{client.client_id}] Второй нашел игру")
                self.accept_game()
    
    def handle_timeout(self, first_client):
        with self.lock:
            print(f"Таймаут ожидания второго клиента для {first_client.client_id}")
            if first_client.client_id in self.timers:
                del self.timers[first_client.client_id]
            
            first_client.send_command("DECLINE")
            first_client.state = "ready"
            first_client.search_status = "idle"
    
    def accept_game(self):
        print("Оба клиента нашли игру, принимаем")
        session_clients = list(self.sessions[SESSION_ID].values())
        for client in session_clients:
            if client.state == "found":
                # Отправляем ACCEPT только если игра найдена недавно
                if hasattr(client, 'found_time') and time.time() - client.found_time < SYNC_TIMEOUT + 5:
                    client.send_command("ACCEPT")
                    client.state = "in_game"
                    client.search_status = "in_game"
                
                if client.client_id in self.timers:
                    self.timers[client.client_id].cancel()
                    del self.timers[client.client_id]

def start_server():
    """Запуск сервера с улучшенной обработкой ошибок"""
    server = SyncServer()
    
    # Настройка сокета с повторным использованием адреса
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    try:
        sock.bind((SERVER_IP, SERVER_PORT))
        sock.listen(5)
        print(f"Сервер синхронизации запущен на {SERVER_IP}:{SERVER_PORT}")
        sock.settimeout(5)  # Таймаут для accept
        
        while True:
            try:
                conn, addr = sock.accept()
                conn.settimeout(30)
                print(f"Новое подключение: {addr}")
                ClientHandler(conn, addr, server)
            except socket.timeout:
                continue
            except Exception as e:
                print(f"Ошибка приема подключения: {e}")
    except Exception as e:
        print(f"Критическая ошибка сервера: {e}")
    finally:
        try:
            sock.close()
        except:
            pass
        print("Сервер остановлен")

if __name__ == "__main__":
    start_server()
