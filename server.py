import socket
import threading
import time
import logging
from queue import Queue

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("dota_matchmaking_server.log"),
        logging.StreamHandler()
    ]
)

class DotaMatchmakingServer:
    def __init__(self, host='0.0.0.0', port=1234):
        self.host = host
        self.port = port
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        self.clients = {}  # client_id: {'socket': socket, 'address': address, 'status': status}
        self.client_lock = threading.Lock()
        self.command_queues = {}  # Очереди команд для каждого клиента
        self.sync_timer = None
        self.found_client = None
        self.search_active = False

    def start(self):
        try:
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(5)
            logging.info(f"Сервер запущен на {self.host}:{self.port}")
            self.accept_clients()
        except Exception as e:
            logging.error(f"Ошибка запуска сервера: {str(e)}")
            self.stop()

    def stop(self):
        logging.info("Остановка сервера...")
        with self.client_lock:
            for client_id, client_info in list(self.clients.items()):
                try:
                    client_info['socket'].close()
                except:
                    pass
            self.clients = {}
            self.command_queues = {}
        try:
            self.server_socket.close()
        except:
            pass

    def accept_clients(self):
        while True:
            try:
                client_socket, address = self.server_socket.accept()
                client_thread = threading.Thread(
                    target=self.handle_client,
                    args=(client_socket, address),
                    daemon=True
                )
                client_thread.start()
                logging.info(f"Новое подключение: {address}")
            except OSError:
                break

    def handle_client(self, client_socket, address):
        client_id = None
        try:
            # Получаем идентификатор клиента
            data = client_socket.recv(1024).decode().strip()
            if not data.startswith("REGISTER:"):
                logging.warning(f"Неверный протокол от {address}: {data}")
                client_socket.close()
                return

            client_id = data.split(':')[1]
            with self.client_lock:
                if client_id in self.clients:
                    logging.warning(f"Клиент {client_id} уже подключен")
                    client_socket.sendall("ERROR:ID_CONFLICT\n".encode())
                    client_socket.close()
                    return

                self.clients[client_id] = {
                    'socket': client_socket,
                    'address': address,
                    'status': 'connected'
                }
                self.command_queues[client_id] = Queue()
                client_socket.sendall("REGISTERED\n".encode())
                logging.info(f"Клиент {client_id} зарегистрирован")

            # Основной цикл обработки сообщений
            while True:
                data = client_socket.recv(1024).decode().strip()
                if not data:
                    break

                logging.info(f"Получено от {client_id}: {data}")
                self.process_client_message(client_id, data)

        except ConnectionResetError:
            logging.warning(f"Соединение с {client_id} разорвано")
        except Exception as e:
            logging.error(f"Ошибка обработки клиента {client_id}: {str(e)}")
        finally:
            with self.client_lock:
                if client_id and client_id in self.clients:
                    del self.clients[client_id]
                    del self.command_queues[client_id]
                    logging.info(f"Клиент {client_id} отключен")
            try:
                client_socket.close()
            except:
                pass

    def process_client_message(self, client_id, message):
        with self.client_lock:
            if message == "READY":
                self.clients[client_id]['status'] = 'ready'
                logging.info(f"Клиент {client_id} готов к поиску")
                self.check_start_search()

            elif message == "FOUND":
                if self.clients[client_id]['status'] != 'searching':
                    logging.warning(f"Клиент {client_id} не в поиске")
                    return

                self.clients[client_id]['status'] = 'found'
                logging.info(f"Клиент {client_id} нашел игру")
                self.initiate_sync(client_id)

            elif message == "ACCEPTED":
                if self.clients[client_id]['status'] == 'found':
                    self.clients[client_id]['status'] = 'accepted'
                    logging.info(f"Клиент {client_id} принял игру")

            elif message == "DECLINED":
                if self.clients[client_id]['status'] == 'found':
                    self.clients[client_id]['status'] = 'ready'
                    logging.info(f"Клиент {client_id} отклонил игру")
                    self.reset_search()

    def check_start_search(self):
        if self.search_active:
            return

        # Проверяем, готовы ли все клиенты
        all_ready = True
        for client in self.clients.values():
            if client['status'] != 'ready':
                all_ready = False
                break

        if all_ready and len(self.clients) >= 2:
            self.start_search()

    def start_search(self):
        logging.info("Все клиенты готовы - начинаем поиск")
        self.search_active = True
        for client_id in self.clients:
            self.clients[client_id]['status'] = 'searching'
            self.send_command(client_id, "START")

    def initiate_sync(self, found_client_id):
        if self.found_client is not None:
            return  # Уже идет синхронизация

        self.found_client = found_client_id
        logging.info(f"Инициируем синхронизацию для {found_client_id}")

        # Запускаем таймер ожидания
        self.sync_timer = threading.Timer(5.0, self.sync_timeout)
        self.sync_timer.start()

        # Проверяем, нашел ли второй клиент игру
        for client_id, client_info in self.clients.items():
            if client_id != found_client_id and client_info['status'] == 'found':
                self.complete_sync()
                return

    def sync_timeout(self):
        logging.info("Таймаут синхронизации")
        with self.client_lock:
            if self.found_client is None:
                return

            # Клиент, который нашел игру, должен отклонить
            self.send_command(self.found_client, "DECLINE")

            # Остальных клиентов возвращаем в поиск
            for client_id, client_info in self.clients.items():
                if client_info['status'] == 'searching':
                    self.send_command(client_id, "CONTINUE")
                elif client_info['status'] == 'found':
                    client_info['status'] = 'searching'

            self.found_client = None
            self.search_active = True

    def complete_sync(self):
        if self.sync_timer:
            self.sync_timer.cancel()

        logging.info("Оба клиента нашли игру - принимаем")
        for client_id in self.clients:
            if self.clients[client_id]['status'] == 'found':
                self.send_command(client_id, "ACCEPT")

        self.found_client = None
        self.search_active = False

    def reset_search(self):
        self.found_client = None
        self.search_active = False
        if self.sync_timer:
            self.sync_timer.cancel()
            self.sync_timer = None

        # Возвращаем всех клиентов в состояние готовности
        for client_id in self.clients:
            if self.clients[client_id]['status'] != 'ready':
                self.clients[client_id]['status'] = 'ready'
                self.send_command(client_id, "RESET")

    def send_command(self, client_id, command):
        try:
            if client_id in self.command_queues:
                self.command_queues[client_id].put(command)
            if client_id in self.clients:
                self.clients[client_id]['socket'].sendall(f"{command}\n".encode())
                logging.info(f"Отправлено {client_id}: {command}")
        except Exception as e:
            logging.error(f"Ошибка отправки команды {client_id}: {str(e)}")

if __name__ == "__main__":
    server = DotaMatchmakingServer()
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()
    except Exception as e:
        logging.error(f"Критическая ошибка: {str(e)}")
        server.stop()
