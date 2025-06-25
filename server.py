import socket
import threading
import time
from collections import defaultdict

class SyncServer:
    def __init__(self, host='0.0.0.0', port=65432):
        self.host = host
        self.port = port
        self.sessions = defaultdict(dict)
        self.lock = threading.Lock()
        self.running = True
        
    def handle_client(self, conn, addr):
        """Обрабатывает подключение клиента"""
        print(f"[Сервер] Подключен клиент: {addr}")
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
                        session[client_id] = {'conn': conn, 'ready': False}
                        print(f"[Сервер] Клиент {client_id} зарегистрирован в сессии {session_id}")
                        conn.sendall(b"ACK:REGISTER")
                        
                    elif command == "READY":
                        client_id = parts[2]
                        if client_id in session:
                            session[client_id]['ready'] = True
                            print(f"[Сервер] Клиент {client_id} готов в сессии {session_id}")
                            conn.sendall(b"ACK:READY")
                            
                            # Проверяем, все ли готовы
                            if all(client['ready'] for client in session.values()):
                                print(f"[Сервер] Все клиенты готовы в сессии {session_id}! Отправляю START")
                                for cid, client in session.items():
                                    try:
                                        client['conn'].sendall(b"START")
                                    except:
                                        print(f"[Сервер] Ошибка отправки START клиенту {cid}")
                                
                    elif command == "STATUS":
                        conn.sendall(f"STATUS:{len(session)}:{sum(1 for c in session.values() if c['ready'])}".encode())
        except Exception as e:
            print(f"[Сервер] Ошибка обработки клиента {addr}: {e}")
        finally:
            conn.close()
            print(f"[Сервер] Клиент {addr} отключен")

    def start(self):
        """Запускает сервер"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.host, self.port))
            s.listen()
            print(f"[Сервер] Сервер синхронизации запущен на {self.host}:{self.port}")
            
            while self.running:
                try:
                    conn, addr = s.accept()
                    threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True).start()
                except:
                    break

    def stop(self):
        """Останавливает сервер"""
        self.running = False

if __name__ == "__main__":
    server = SyncServer()
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()
        print("\n[Сервер] Сервер остановлен")
