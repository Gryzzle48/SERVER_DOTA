import socket
import threading
import time
from collections import defaultdict

class SyncServer:
    def __init__(self, host='0.0.0.0', port=1234, min_clients=2):
        self.host = host
        self.port = port
        self.sessions = defaultdict(dict)
        self.connections = {}
        self.client_sessions = {}
        self.lock = threading.Lock()
        self.running = True
        self.session_timeout = 600
        self.min_clients = min_clients

    def handle_client(self, conn, addr):
        print(f"[Сервер] Подключен клиент: {addr}")
        client_id = None
        session_id = None
        
        try:
            while self.running:
                data = conn.recv(1024).decode()
                if not data:
                    break
                    
                parts = data.split(':')
                if len(parts) < 2:
                    continue
                    
                command = parts[0]
                session_id = parts[1]
                
                with self.lock:
                    if command == "REGISTER":
                        if len(parts) < 3:
                            continue
                        client_id = parts[2]
                        
                        # Очистка предыдущей регистрации
                        if client_id in self.client_sessions:
                            old_session = self.client_sessions[client_id]
                            if client_id in self.sessions[old_session]:
                                del self.sessions[old_session][client_id]
                        
                        self.sessions[session_id][client_id] = {
                            'ready': False, 
                            'last_active': time.time()
                        }
                        self.connections[client_id] = conn
                        self.client_sessions[client_id] = session_id
                        print(f"[Сервер] Клиент {client_id} зарегистрирован в сессии {session_id}. Всего в сессии: {len(self.sessions[session_id])}")
                        conn.sendall(b"ACK:REGISTER")
                        
                    elif command == "READY":
                        if len(parts) < 3:
                            continue
                        client_id = parts[2]
                        
                        # Проверка валидности сессии
                        if session_id not in self.sessions:
                            continue
                        if client_id not in self.sessions[session_id]:
                            continue
                            
                        self.sessions[session_id][client_id]['ready'] = True
                        self.sessions[session_id][client_id]['last_active'] = time.time()
                        print(f"[Сервер] Клиент {client_id} готов в сессии {session_id}")
                        conn.sendall(b"ACK:READY")
                        
                        session = self.sessions[session_id]
                        total_clients = len(session)
                        ready_clients = sum(1 for c in session.values() if c['ready'])
                        
                        # Проверяем минимальное количество клиентов
                        if total_clients >= self.min_clients and ready_clients == total_clients:
                            print(f"[Сервер] Все клиенты ({ready_clients}/{total_clients}) готовы! Отправляю START для сессии {session_id}")
                            for cid in session:
                                if cid in self.connections:
                                    try:
                                        self.connections[cid].sendall(b"START")
                                        # Сбрасываем статус готовности после отправки START
                                        self.sessions[session_id][cid]['ready'] = False
                                    except:
                                        print(f"[Сервер] Ошибка отправки START клиенту {cid}")
                    
                    elif command == "STATUS":
                        if session_id in self.sessions:
                            session = self.sessions[session_id]
                            total = len(session)
                            ready = sum(1 for c in session.values() if c['ready'])
                            conn.sendall(f"STATUS:{total}:{ready}:{self.min_clients}".encode())
                        else:
                            conn.sendall(f"STATUS:0:0:{self.min_clients}".encode())
                    
                    elif command == "PING":
                        if len(parts) < 3:
                            continue
                        client_id = parts[2]
                        if session_id in self.sessions and client_id in self.sessions[session_id]:
                            self.sessions[session_id][client_id]['last_active'] = time.time()
                            conn.sendall(b"PONG")
        
        except Exception as e:
            print(f"[Сервер] Ошибка обработки клиента {addr}: {e}")
        finally:
            with self.lock:
                if client_id:
                    if client_id in self.connections:
                        del self.connections[client_id]
                    
                    if client_id in self.client_sessions:
                        session_id = self.client_sessions[client_id]
                        if session_id in self.sessions and client_id in self.sessions[session_id]:
                            del self.sessions[session_id][client_id]
                            print(f"[Сервер] Клиент {client_id} удален из сессии {session_id}")
                        
                            if not self.sessions[session_id]:
                                del self.sessions[session_id]
                                print(f"[Сервер] Сессия {session_id} удалена")
                        
                        del self.client_sessions[client_id]
            
            conn.close()
            print(f"[Сервер] Клиент {addr} отключен")

    def clean_inactive_sessions(self):
        while self.running:
            time.sleep(60)
            with self.lock:
                now = time.time()
                to_remove = []
                
                for session_id, session in list(self.sessions.items()):
                    for client_id, client_data in list(session.items()):
                        if now - client_data['last_active'] > self.session_timeout:
                            print(f"[Сервер] Удаление неактивного клиента {client_id} из сессии {session_id}")
                            
                            if client_id in self.connections:
                                try:
                                    self.connections[client_id].close()
                                except:
                                    pass
                                del self.connections[client_id]
                            
                            del session[client_id]
                            
                            if client_id in self.client_sessions:
                                del self.client_sessions[client_id]
                    
                    if not session:
                        del self.sessions[session_id]
                        print(f"[Сервер] Сессия {session_id} удалена по таймауту")

    def start(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.host, self.port))
            s.listen(10)
            print(f"[Сервер] Сервер запущен на {self.host}:{self.port}")
            
            threading.Thread(target=self.clean_inactive_sessions, daemon=True).start()
            
            while self.running:
                try:
                    conn, addr = s.accept()
                    threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()
                except Exception as e:
                    if self.running:
                        print(f"[Сервер] Ошибка подключения: {e}")

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
    server = SyncServer(min_clients=2)
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()
        print("\n[Сервер] Сервер остановлен")
