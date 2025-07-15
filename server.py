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
        # Запускаем очистку неактивных клиентов
        self.start_inactive_cleaner()

    def start_inactive_cleaner(self):
        """Фоновая задача для очистки неактивных клиентов"""
        def cleaner():
            while self.running:
                time.sleep(60)  # Проверка каждую минуту
                with self.lock:
                    now = time.time()
                    for session, clients in list(self.sessions.items()):
                        for cid, data in list(clients.items()):
                            # Удаляем клиентов неактивных более 5 минут
                            if not data['active'] and now - data['last_seen'] > 300:
                                del self.sessions[session][cid]
                                print(f"Удален неактивный клиент: {cid} из сессии {session}")
        
        threading.Thread(target=cleaner, daemon=True).start()

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
                        self._handle_register(conn, session, cid, addr)
                        client_id = cid
                        session_id = session
                        
                    elif command == "READY":
                        self._handle_ready(session, cid)
                        
                    elif command == "FOUND":
                        self._handle_found(session, cid)
                        
                    elif command == "RECONNECT":
                        self._handle_reconnect(conn, session, cid, addr)
                        
                    elif command == "PONG":
                        if session in self.sessions and cid in self.sessions[session]:
                            self.sessions[session][cid]['last_seen'] = time.time()
                    
            # Обработка таймера игры
            if session_id and client_id:
                self._check_game_timer(session_id)
                
        except Exception as e:
            print(f"Ошибка обработки клиента: {e}")
        finally:
            self._mark_inactive(client_id, session_id)

    def _handle_register(self, conn, session, cid, addr):
        now = time.time()
        # Если клиент уже существует, обновляем соединение
        if session in self.sessions and cid in self.sessions[session]:
            client_data = self.sessions[session][cid]
            client_data['conn'] = conn
            client_data['active'] = True
            client_data['last_seen'] = now
            self.connections[cid] = conn
            print(f"Клиент {cid} перерегистрирован в сессии {session}")
        else:
            # Новый клиент
            self.sessions[session][cid] = {
                'ready': False,
                'found': False,
                'active': True,
                'last_seen': now,
                'conn': conn,
                'pending_commands': []
            }
            self.connections[cid] = conn
            print(f"Клиент {cid} зарегистрирован в сессии {session}")

        # Отправляем ожидающие команды
        self._send_pending_commands(session, cid)

    def _handle_ready(self, session, cid):
        if session in self.sessions and cid in self.sessions[session]:
            self.sessions[session][cid]['ready'] = True
            self.sessions[session][cid]['last_seen'] = time.time()
            
            print(f"Клиент {cid} готов к поиску")
            
            if self._check_start_condition(session):
                print(f"Все клиенты готовы, отправляю START для сессии {session}")
                self._broadcast_to_session(session, "START")

    def _handle_found(self, session, cid):
        if session in self.sessions and cid in self.sessions[session]:
            print(f"Клиент {cid} нашел игру")
            self.sessions[session][cid]['found'] = True
            self.sessions[session][cid]['last_seen'] = time.time()
            
            # Запуск/сброс таймера подтверждения
            if session not in self.game_timers:
                print(f"Запуск таймера подтверждения для сессии {session}")
                self.game_timers[session] = {
                    'timer': threading.Timer(7.0, self._handle_game_timeout, [session]),
                    'found_clients': set()
                }
                self.game_timers[session]['timer'].start()
            else:
                # Сбрасываем таймер при новом FOUND
                try:
                    self.game_timers[session]['timer'].cancel()
                except:
                    pass
                self.game_timers[session]['timer'] = threading.Timer(7.0, self._handle_game_timeout, [session])
                self.game_timers[session]['timer'].start()
                print(f"Сброс таймера для сессии {session}")
                
            # Проверка подтверждения игры
            self.game_timers[session]['found_clients'].add(cid)
            self._check_game_confirmation(session)

    def _handle_reconnect(self, conn, session, cid, addr):
        if session in self.sessions and cid in self.sessions[session]:
            client_data = self.sessions[session][cid]
            client_data['conn'] = conn
            client_data['active'] = True
            client_data['last_seen'] = time.time()
            self.connections[cid] = conn
            
            print(f"Клиент {cid} переподключен")
            
            # Отправляем ожидающие команды
            self._send_pending_commands(session, cid)

    def _send_pending_commands(self, session, cid):
        """Отправляет все ожидающие команды клиенту"""
        if not self.sessions[session][cid]['pending_commands']:
            return
            
        print(f"Отправка ожидающих команд для {cid} ({len(self.sessions[session][cid]['pending_commands']})")
        
        try:
            for command in self.sessions[session][cid]['pending_commands']:
                self.sessions[session][cid]['conn'].sendall(command.encode())
                print(f"Отправлена ожидающая команда: {command} для {cid}")
        except Exception as e:
            print(f"Ошибка отправки ожидающих команд для {cid}: {e}")
        
        # Очищаем очередь команд
        self.sessions[session][cid]['pending_commands'] = []

    def _check_game_confirmation(self, session):
        """Проверяет подтверждение игры"""
        if session not in self.game_timers:
            return
            
        timer_data = self.game_timers[session]
        session_data = self.sessions.get(session, {})
        
        # Все активные клиенты нашли игру
        active_clients = [cid for cid, client in session_data.items() if client['active']]
        if len(timer_data['found_clients']) == len(active_clients):
            print(f"Все активные клиенты нашли игру, подтверждаем")
            self._broadcast_to_session(session, "ACCEPT")
            self._cleanup_game_timer(session)

    def _handle_game_timeout(self, session):
        """Обработка таймаута подтверждения игры"""
        with self.lock:
            if session not in self.game_timers:
                return
                
            timer_data = self.game_timers[session]
            session_data = self.sessions.get(session, {})
            
            print(f"Таймаут подтверждения игры для сессии {session}")
            print(f"Нашли игру: {len(timer_data['found_clients'])}/{len(session_data)} клиентов")
            
            # Отправляем SKIP всем клиентам (даже неактивным)
            for cid, client in session_data.items():
                # Сбрасываем статус найденной игры
                self.sessions[session][cid]['found'] = False
                
                if client['active']:
                    try:
                        print(f"Отправка SKIP клиенту {cid}")
                        client['conn'].sendall(b"SKIP")
                    except Exception as e:
                        print(f"Ошибка отправки SKIP клиенту {cid}: {e}")
                else:
                    # Для неактивных сохраняем команду в ожидание
                    self.sessions[session][cid]['pending_commands'].append("SKIP")
                    print(f"SKIP сохранен для неактивного клиента {cid}")
            
            # Сбрасываем таймер
            self._cleanup_game_timer(session)

    def _broadcast_to_session(self, session, command):
        """Отправляет команду всем клиентам в сессии"""
        for cid, client_data in self.sessions[session].items():
            if client_data['active']:
                try:
                    client_data['conn'].sendall(command.encode())
                    print(f"Отправлено {command} клиенту {cid}")
                except Exception as e:
                    print(f"Ошибка отправки {command} клиенту {cid}: {e}")
                    client_data['active'] = False
            else:
                # Сохраняем команду для неактивных клиентов
                client_data['pending_commands'].append(command)
                print(f"Команда {command} сохранена для неактивного клиента {cid}")

    def _cleanup_game_timer(self, session):
        """Очищает таймер игры"""
        if session in self.game_timers:
            try:
                self.game_timers[session]['timer'].cancel()
            except:
                pass
            del self.game_timers[session]

    def _check_start_condition(self, session):
        """Проверяет возможность начать поиск"""
        if session not in self.sessions:
            return False
            
        session_data = self.sessions[session]
        active_clients = [c for c in session_data.values() if c['active'] and c['ready']]
        return len(active_clients) >= self.min_clients

    def _mark_inactive(self, cid, session_id):
        """Помечает клиента как неактивного"""
        with self.lock:
            if cid and session_id and session_id in self.sessions and cid in self.sessions[session_id]:
                self.sessions[session_id][cid]['active'] = False
                print(f"Клиент {cid} помечен как неактивный в сессии {session_id}")
                
            if cid in self.connections:
                del self.connections[cid]

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
