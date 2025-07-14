import socket
import threading
import time
from collections import defaultdict
import queue

class SyncServer:
    def __init__(self, host='0.0.0.0', port=1234):
        self.host = host
        self.port = port
        self.sessions = defaultdict(dict)
        self.connections = {}
        self.client_sessions = {}
        self.lock = threading.Lock()
        self.running = True
        self.session_timeout = 600
        self.match_events = {}
        self.pending_matches = queue.Queue()
        
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
                            'last_active': time.time(),
                            'match_found': False
                        }
                        self.connections[client_id] = conn
                        self.client_sessions[client_id] = session_id
                        print(f"[Сервер] Клиент {client_id} зарегистрирован в сессии {session_id}. Всего в сессии: {len(self.sessions[session_id])}")
                        conn.sendall(b"ACK:REGISTER")
                        
                    elif command == "READY":
                        if len(parts) < 3:
                            continue
                        client_id = parts[2]
                        
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
                        
                        # Отправляем START если есть хотя бы 2 готовых клиента
                        if total_clients >= 2 and ready_clients >= 2:
                            print(f"[Сервер] Минимум 2 клиента ({ready_clients}/{total_clients}) готовы! Отправляю START для сессии {session_id}")
                            for cid in session:
                                if cid in self.connections and session[cid]['ready']:
                                    try:
                                        self.connections[cid].sendall(b"START")
                                        session[cid]['ready'] = False
                                    except:
                                        print(f"[Сервер] Ошибка отправки START клиенту {cid}")
                    
                    elif command == "MATCH_FOUND":
                        if len(parts) < 3:
                            continue
                        client_id = parts[2]
                        
                        if session_id not in self.sessions:
                            continue
                        if client_id not in self.sessions[session_id]:
                            continue
                            
                        # Помечаем клиента как нашедшего игру
                        self.sessions[session_id][client_id]['match_found'] = True
                        print(f"[Сервер] Клиент {client_id} нашел игру в сессии {session_id}")
                        conn.sendall(b"ACK:MATCH_FOUND")
                        
                        # Добавляем в очередь для обработки
                        self.pending_matches.put((session_id, client_id))
        
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

    def handle_match_acceptance(self):
        """Обрабатывает принятие найденных игр"""
        while self.running:
            try:
                session_id, client_id = self.pending_matches.get(timeout=1)
                
                with self.lock:
                    # Находим второго клиента в сессии
                    other_client = None
                    for cid in self.sessions[session_id]:
                        if cid != client_id:
                            other_client = cid
                            break
                    
                    if not other_client:
                        print(f"[Сервер] В сессии {session_id} только один клиент")
                        if client_id in self.connections:
                            self.connections[client_id].sendall(b"SKIP")
                        continue
                    
                    # Проверяем, нашел ли игру второй клиент
                    second_found = False
                    for _ in range(5):  # 5 секунд ожидания
                        if other_client in self.sessions[session_id] and \
                           self.sessions[session_id][other_client].get('match_found', False):
                            second_found = True
                            break
                        time.sleep(1)
                    
                    # Принимаем решение
                    if second_found:
                        print(f"[Сервер] Обе стороны нашли игру, разрешаем прием")
                        self.connections[client_id].sendall(b"ACCEPT")
                        self.connections[other_client].sendall(b"ACCEPT")
                    else:
                        print(f"[Сервер] Вторая сторона не нашла игру, пропускаем")
                        self.connections[client_id].sendall(b"SKIP")
                        # Сбрасываем флаг для второго клиента
                        if other_client in self.sessions[session_id]:
                            self.sessions[session_id][other_client]['match_found'] = False
                        
            except queue.Empty:
                continue
            except Exception as e:
                print(f"[Сервер] Ошибка обработки матча: {e}")

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
            
            # Запускаем потоки для обработки
            threading.Thread(target=self.clean_inactive_sessions, daemon=True).start()
            threading.Thread(target=self.handle_match_acceptance, daemon=True).start()
            
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
    server = SyncServer()
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()
        print("\n[Сервер] Сервер остановлен")
