import socket
import threading
import time
from collections import defaultdict

class SyncServer:
    def __init__(self, host='0.0.0.0', port=1234):
        self.host = host
        self.port = port
        self.sessions = defaultdict(dict)
        self.connections = {}  # Храним активные соединения
        self.lock = threading.Lock()
        self.running = True
        self.session_timeout = 600  # 10 минут бездействия
        
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
                    session = self.sessions[session_id]
                    
                    if command == "REGISTER":
                        client_id = parts[2]
                        session[client_id] = {'ready': False, 'last_active': time.time()}
                        self.connections[client_id] = conn  # Сохраняем соединение
                        print(f"[Сервер] Клиент {client_id} зарегистрирован в сессии {session_id}")
                        conn.sendall(b"ACK:REGISTER")
                        
                    elif command == "READY":
                        client_id = parts[2]
                        if client_id in session:
                            session[client_id]['ready'] = True
                            session[client_id]['last_active'] = time.time()
                            print(f"[Сервер] Клиент {client_id} готов в сессии {session_id}")
                            conn.sendall(b"ACK:READY")
                            
                            # Проверяем, все ли готовы
                            if all(client['ready'] for client in session.values()):
                                print(f"[Сервер] Все клиенты готовы в сессии {session_id}! Отправляю START")
                                for cid in session:
                                    if cid in self.connections:
                                        try:
                                            self.connections[cid].sendall(b"START")
                                        except:
                                            print(f"[Сервер] Ошибка отправки START клиенту {cid}")
                                
                    elif command == "STATUS":
                        conn.sendall(f"STATUS:{len(session)}:{sum(1 for c in session.values() if c['ready'])}".encode())
                    elif command == "PING":
                        if len(parts) > 2 and parts[2] in session:
                            session[parts[2]]['last_active'] = time.time()
                            conn.sendall(b"PONG")
        
        except Exception as e:
            print(f"[Сервер] Ошибка обработки клиента {addr}: {e}")
        finally:
            conn.close()
            print(f"[Сервер] Клиент {addr} отключен")
            
            # Удаляем из активных соединений
            if client_id and client_id in self.connections:
                del self.connections[client_id]
    
    def clean_inactive_sessions(self):
        """Очищает неактивные сессии"""
        while self.running:
            time.sleep(60)
            with self.lock:
                now = time.time()
                inactive_sessions = []
                
                for session_id, session in list(self.sessions.items()):
                    # Удаляем неактивных клиентов
                    inactive_clients = [cid for cid, client in session.items() 
                                       if now - client['last_active'] > self.session_timeout]
                    
                    for cid in inactive_clients:
                        print(f"[Сервер] Удаляю неактивного клиента {cid} из сессии {session_id}")
                        
                        # Закрываем соединение, если оно есть
                        if cid in self.connections:
                            try:
                                self.connections[cid].close()
                            except:
                                pass
                            del self.connections[cid]
                            
                        del session[cid]
                    
                    # Удаляем пустые сессии
                    if not session:
                        print(f"[Сервер] Удаляю пустую сессию {session_id}")
                        inactive_sessions.append(session_id)
                
                for session_id in inactive_sessions:
                    del self.sessions[session_id]

    def start(self):
        """Запускает сервер"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.host, self.port))
            s.listen(10)  # Увеличиваем очередь подключений
            print(f"[Сервер] Сервер синхронизации запущен на {self.host}:{self.port}")
            
            # Запускаем очистку неактивных сессий
            threading.Thread(target=self.clean_inactive_sessions, daemon=True).start()
            
            while self.running:
                try:
                    conn, addr = s.accept()
                    threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()
                except Exception as e:
                    print(f"[Сервер] Ошибка приема подключения: {e}")

    def stop(self):
        """Останавливает сервер"""
        self.running = False
        # Закрываем все активные соединения
        with self.lock:
            for conn in self.connections.values():
                try:
                    conn.close()
                except:
                    pass
            self.connections.clear()

if __name__ == "__main__":
    server = SyncServer()
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()
        print("\n[Сервер] Сервер остановлен")
