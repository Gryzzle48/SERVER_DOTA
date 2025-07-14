import socket
import threading
import time
import select  # Добавляем импорт модуля select
from collections import defaultdict

class SyncServer:
    def __init__(self, host='0.0.0.0', port=1234, min_clients=2):
        self.host = host
        self.port = port
        self.sessions = defaultdict(dict)  # session_id -> {client_id: client_data}
        self.connections = {}  # client_id -> socket
        self.client_sessions = {}  # client_id -> session_id
        self.found_games = defaultdict(dict)  # session_id -> {client_id: timestamp}
        self.pending_commands = defaultdict(list)  # client_id -> list of commands
        self.lock = threading.Lock()
        self.running = True
        self.session_timeout = 60
        self.min_clients = min_clients
        self.game_timeout = 10  # 5 секунд на ожидание второго клиента
        self.command_retention = 30  # Хранить команды 30 секунд

    def handle_client(self, conn, addr):
        print(f"[Сервер] Подключен клиент: {addr}")
        client_id = None
        session_id = None
        
        try:
            while self.running:
                try:
                    # Проверяем данные с помощью select
                    ready, _, _ = select.select([conn], [], [], 1.0)
                    if not ready:
                        continue
                    
                    data = conn.recv(1024).decode()
                    if not data:
                        break
                except (socket.timeout, BlockingIOError):
                    continue
                except (ConnectionResetError, ConnectionAbortedError):
                    break
                except Exception as e:
                    print(f"[Сервер] Ошибка чтения данных: {e}")
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
                        
                        # Регистрируем клиента
                        self.sessions[session_id][client_id] = {
                            'ready': False, 
                            'last_active': time.time(),
                            'connected': True
                        }
                        self.connections[client_id] = conn
                        self.client_sessions[client_id] = session_id
                        
                        # Отправляем ожидающие команды
                        if client_id in self.pending_commands:
                            for cmd in self.pending_commands[client_id]:
                                try:
                                    conn.sendall(cmd.encode())
                                except:
                                    print(f"[Сервер] Ошибка отправки ожидающей команды {cmd} клиенту {client_id}")
                            self.pending_commands[client_id] = []
                        
                        print(f"[Сервер] Клиент {client_id} зарегистрирован в сессии {session_id}. Всего в сессии: {len(self.sessions[session_id])}")
                        try:
                            conn.sendall(b"ACK:REGISTER")
                        except:
                            print(f"[Сервер] Ошибка отправки ACK:REGISTER клиенту {client_id}")
                        
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
                        try:
                            conn.sendall(b"ACK:READY")
                        except:
                            print(f"[Сервер] Ошибка отправки ACK:READY клиенту {client_id}")
                        
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
                                        # Сохраняем команду для последующей отправки
                                        self.pending_commands[cid].append("START")
                                        print(f"[Сервер] Ошибка отправки START клиенту {cid}, команда сохранена")
                    
                    elif command == "FOUND_GAME":
                        if len(parts) < 3:
                            continue
                        client_id = parts[2]
                        
                        if session_id not in self.sessions:
                            continue
                        if client_id not in self.sessions[session_id]:
                            continue
                            
                        print(f"[Сервер] Клиент {client_id} нашел игру в сессии {session_id}")
                        
                        # Сохраняем информацию о найденной игре
                        self.found_games[session_id][client_id] = time.time()
                        
                        # Проверяем, все ли клиенты нашли игру
                        self.check_game_found(session_id)
                    
                    elif command == "STATUS":
                        if session_id in self.sessions:
                            session = self.sessions[session_id]
                            total = len(session)
                            ready = sum(1 for c in session.values() if c['ready'])
                            try:
                                conn.sendall(f"STATUS:{total}:{ready}:{self.min_clients}".encode())
                            except:
                                print(f"[Сервер] Ошибка отправки STATUS клиенту {client_id}")
                        else:
                            try:
                                conn.sendall(f"STATUS:0:0:{self.min_clients}".encode())
                            except:
                                print(f"[Сервер] Ошибка отправки STATUS клиенту {client_id}")
                    
                    elif command == "PING":
                        if len(parts) < 3:
                            continue
                        client_id = parts[2]
                        if session_id in self.sessions and client_id in self.sessions[session_id]:
                            self.sessions[session_id][client_id]['last_active'] = time.time()
                            try:
                                conn.sendall(b"PONG")
                            except:
                                print(f"[Сервер] Ошибка отправки PONG клиенту {client_id}")
        
        except Exception as e:
            print(f"[Сервер] Ошибка обработки клиента {addr}: {e}")
        finally:
            with self.lock:
                if client_id:
                    # Помечаем клиента как отключенного, но сохраняем данные
                    if session_id and session_id in self.sessions and client_id in self.sessions[session_id]:
                        self.sessions[session_id][client_id]['connected'] = False
                    
                    if client_id in self.connections:
                        del self.connections[client_id]
                    
                    print(f"[Сервер] Клиент {client_id} отключен")

    def check_game_found(self, session_id):
        """Проверяет, все ли клиенты нашли игру, и отправляет соответствующие команды"""
        if session_id not in self.sessions or session_id not in self.found_games:
            return
            
        session_clients = set(self.sessions[session_id].keys())
        found_clients = set(self.found_games[session_id].keys())
        current_time = time.time()
        
        # Проверяем, все ли клиенты нашли игру
        if found_clients == session_clients:
            print(f"[Сервер] Все клиенты в сессии {session_id} нашли игру! Отправляю ACCEPT")
            for cid in found_clients:
                if cid in self.connections:
                    try:
                        self.connections[cid].sendall(b"ACCEPT")
                    except:
                        # Сохраняем команду для последующей отправки
                        self.pending_commands[cid].append("ACCEPT")
                        print(f"[Сервер] Ошибка отправки ACCEPT клиенту {cid}, команда сохранена")
                else:
                    # Сохраняем команду для последующей отправки
                    self.pending_commands[cid].append("ACCEPT")
            del self.found_games[session_id]
            return
        
        # Проверяем таймауты для каждого клиента
        for client_id, found_time in list(self.found_games[session_id].items()):
            if current_time - found_time > self.game_timeout:
                print(f"[Сервер] Таймаут ожидания игры для клиента {client_id} в сессии {session_id}. Отправляю SKIP")
                if client_id in self.connections:
                    try:
                        self.connections[client_id].sendall(b"SKIP")
                    except:
                        # Сохраняем команду для последующей отправки
                        self.pending_commands[client_id].append("SKIP")
                        print(f"[Сервер] Ошибка отправки SKIP клиенту {client_id}, команда сохранена")
                else:
                    # Сохраняем команду для последующей отправки
                    self.pending_commands[client_id].append("SKIP")
                
                # Удаляем клиента из списка ожидающих
                del self.found_games[session_id][client_id]
        
        # Если не осталось клиентов, ожидающих подтверждения
        if not self.found_games[session_id]:
            del self.found_games[session_id]

    def clean_inactive_sessions(self):
        """Очищает неактивные сессии и старые команды"""
        while self.running:
            time.sleep(10)
            with self.lock:
                now = time.time()
                
                # Очищаем старые команды
                for client_id in list(self.pending_commands.keys()):
                    # Удаляем клиента, если он неактивен
                    if client_id in self.client_sessions:
                        session_id = self.client_sessions[client_id]
                        if session_id in self.sessions and client_id in self.sessions[session_id]:
                            last_active = self.sessions[session_id][client_id]['last_active']
                            if now - last_active > self.session_timeout:
                                del self.pending_commands[client_id]
                                continue
                    
                    # Удаляем команды для несуществующих клиентов
                    if client_id not in self.client_sessions:
                        del self.pending_commands[client_id]
                
                # Очищаем неактивные сессии
                for session_id, session in list(self.sessions.items()):
                    # Удаляем неактивных клиентов
                    for client_id, client_data in list(session.items()):
                        if now - client_data['last_active'] > self.session_timeout:
                            print(f"[Сервер] Удаление неактивного клиента {client_id} из сессии {session_id}")
                            
                            # Удаляем из списка найденных игр
                            if session_id in self.found_games and client_id in self.found_games[session_id]:
                                del self.found_games[session_id][client_id]
                            
                            # Удаляем из сессии
                            del session[client_id]
                            
                            # Удаляем из client_sessions
                            if client_id in self.client_sessions:
                                del self.client_sessions[client_id]
                    
                    # Если сессия пуста, удаляем ее
                    if not session:
                        del self.sessions[session_id]
                        print(f"[Сервер] Сессия {session_id} удалена по таймауту")
                        
                        # Удаляем связанные данные
                        if session_id in self.found_games:
                            del self.found_games[session_id]

    def start(self):
        """Запускает сервер"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.host, self.port))
            s.listen(10)
            print(f"[Сервер] Сервер запущен на {self.host}:{self.port}")
            
            # Запускаем поток очистки
            threading.Thread(target=self.clean_inactive_sessions, daemon=True).start()
            
            while self.running:
                try:
                    conn, addr = s.accept()
                    # Устанавливаем таймаут для соединения
                    conn.settimeout(10.0)
                    threading.Thread(target=self.handle_client, args=(conn, addr)).start()
                except Exception as e:
                    if self.running:
                        print(f"[Сервер] Ошибка подключения: {e}")

    def stop(self):
        """Останавливает сервер"""
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
