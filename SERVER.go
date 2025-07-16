package main

import (
	"bufio"
	"fmt"
	"net"
	"strings"
	"sync"
	"time"
)

const (
	PORT = ":1234"
)

type Client struct {
	Conn     net.Conn
	ID       string
	Ready    bool
	Found    bool
	InSearch bool
}

type Session struct {
	Clients    map[string]*Client
	MatchFound chan string
	Timer      *time.Timer
	Mutex      sync.Mutex
	FoundCount int
}

var (
	sessions   = make(map[string]*Session)
	sessionsMu sync.Mutex
)

func main() {
	listener, err := net.Listen("tcp", PORT)
	if err != nil {
		fmt.Println("Ошибка запуска сервера:", err)
		return
	}
	defer listener.Close()
	fmt.Printf("Сервер запущен на порту %s\n", PORT)

	for {
		conn, err := listener.Accept()
		if err != nil {
			fmt.Println("Ошибка подключения:", err)
			continue
		}
		go handleConnection(conn)
	}
}

func handleConnection(conn net.Conn) {
	// Установка таймаутов
	conn.SetReadDeadline(time.Now().Add(300 * time.Second))
	conn.SetWriteDeadline(time.Now().Add(300 * time.Second))
	
	// Установка TCP keepalive
	if tcpConn, ok := conn.(*net.TCPConn); ok {
		tcpConn.SetKeepAlive(true)
		tcpConn.SetKeepAlivePeriod(30 * time.Second)
	}
	
	// Создаем клиентскую структуру
	client := &Client{
		Conn: conn,
	}
	
	// Обработка отключения клиента
	defer func() {
		if client.ID != "" {
			fmt.Printf("Клиент %s отключен\n", client.ID)
			removeClientFromSession(client.ID)
		}
		conn.Close()
	}()

	scanner := bufio.NewScanner(conn)
	for scanner.Scan() {
		// Сброс таймаута при получении данных
		conn.SetReadDeadline(time.Now().Add(300 * time.Second))
		
		msg := scanner.Text()
		parts := strings.Split(msg, ":")
		if len(parts) < 3 {
			sendResponse(conn, "ERROR:INVALID_FORMAT")
			continue
		}

		cmd, sessionID, clientID := parts[0], parts[1], parts[2]
		client.ID = clientID  // Устанавливаем ID клиента

		switch cmd {
		case "REGISTER":
			handleRegister(client, sessionID)
		case "READY":
			handleReady(client, sessionID)
		case "FOUND_GAME":
			handleFoundGame(client, sessionID)
		default:
			sendResponse(conn, "ERROR:UNKNOWN_COMMAND")
		}
	}

	if err := scanner.Err(); err != nil {
		if netErr, ok := err.(net.Error); ok && netErr.Timeout() {
			fmt.Println("Таймаут чтения:", err)
		} else {
			fmt.Println("Ошибка чтения:", err)
		}
	}
}

func removeClientFromSession(clientID string) {
	sessionsMu.Lock()
	defer sessionsMu.Unlock()
	
	for sessionID, session := range sessions {
		session.Mutex.Lock()
		if _, exists := session.Clients[clientID]; exists {
			delete(session.Clients, clientID)
			fmt.Printf("Клиент %s удален из сессии %s\n", clientID, sessionID)
			
			// Удаляем сессию если она пуста
			if len(session.Clients) == 0 {
				delete(sessions, sessionID)
				fmt.Printf("Сессия %s удалена\n", sessionID)
			}
		}
		session.Mutex.Unlock()
	}
}

func handleRegister(client *Client, sessionID string) {
	sessionsMu.Lock()
	defer sessionsMu.Unlock()

	if _, exists := sessions[sessionID]; !exists {
		sessions[sessionID] = &Session{
			Clients:    make(map[string]*Client),
			MatchFound: make(chan string, 2),
		}
	}

	session := sessions[sessionID]
	session.Mutex.Lock()
	defer session.Mutex.Unlock()

	session.Clients[client.ID] = client
	sendResponse(client.Conn, "ACK:REGISTER")
}

func handleReady(client *Client, sessionID string) {
	sessionsMu.Lock()
	session, exists := sessions[sessionID]
	sessionsMu.Unlock()

	if !exists {
		sendResponse(client.Conn, "ERROR:NO_SESSION")
		return
	}

	session.Mutex.Lock()
	client.Ready = true
	client.InSearch = false
	session.Mutex.Unlock()

	// Проверяем готовность двух клиентов
	readyClients := []*Client{}
	session.Mutex.Lock()
	for _, c := range session.Clients {
		if c.Ready && !c.InSearch {
			readyClients = append(readyClients, c)
		}
	}
	session.Mutex.Unlock()

	if len(readyClients) >= 2 {
		// Запускаем поиск для пары клиентов
		for i := 0; i < 2; i++ {
			session.Mutex.Lock()
			readyClients[i].InSearch = true
			if !sendResponse(readyClients[i].Conn, "START_SEARCH") {
				fmt.Printf("Ошибка отправки START_SEARCH клиенту %s\n", readyClients[i].ID)
			} else {
				// Успешная отправка - сбрасываем флаг готовности
				readyClients[i].Ready = false
			}
			session.Mutex.Unlock()
		}
	} else {
		sendResponse(client.Conn, "ACK:READY")
	}
}

func handleFoundGame(client *Client, sessionID string) {
	sessionsMu.Lock()
	session, exists := sessions[sessionID]
	sessionsMu.Unlock()

	if !exists {
		sendResponse(client.Conn, "ERROR:NO_SESSION")
		return
	}

	session.Mutex.Lock()
	defer session.Mutex.Unlock()

	// Помечаем клиента как нашедшего игру
	client.Found = true
	
	// Увеличиваем счетчик найденных игр в сессии
	session.FoundCount++

	// Если оба клиента нашли игру - принимаем
	if session.FoundCount >= 2 {
		for _, c := range session.Clients {
			if c.Found {
				if !sendResponse(c.Conn, "ACCEPT_MATCH") {
					fmt.Printf("Ошибка отправки ACCEPT_MATCH клиенту %s\n", c.ID)
				}
				// Сбрасываем статус
				c.Found = false
			}
		}
		// Сбрасываем счетчик
		session.FoundCount = 0
		
		// Останавливаем и сбрасываем таймер если активен
		if session.Timer != nil {
			if !session.Timer.Stop() {
				<-session.Timer.C
			}
			session.Timer = nil
		}
	} else if session.Timer == nil {
		// Запускаем таймер только для первого клиента
		session.Timer = time.NewTimer(5 * time.Second)
		go func() {
			<-session.Timer.C
			handleMatchTimeout(session)
		}()
	}
}

func handleMatchTimeout(session *Session) {
	session.Mutex.Lock()
	defer session.Mutex.Unlock()

	// Отклоняем игру у всех клиентов, которые нашли игру
	for _, c := range session.Clients {
		if c.Found {
			if !sendResponse(c.Conn, "DECLINE_MATCH") {
				fmt.Printf("Ошибка отправки DECLINE_MATCH клиенту %s\n", c.ID)
			}
			c.Found = false
		}
	}
	
	// Сбрасываем счетчик и таймер
	session.FoundCount = 0
	session.Timer = nil
}

func sendResponse(conn net.Conn, msg string) bool {
	for i := 0; i < 3; i++ {
		// Устанавливаем таймаут для каждой попытки
		conn.SetWriteDeadline(time.Now().Add(30 * time.Second))
		
		_, err := fmt.Fprintln(conn, msg)
		if err == nil {
			return true
		}
		
		if netErr, ok := err.(net.Error); ok && netErr.Timeout() {
			fmt.Printf("Таймаут отправки (попытка %d/3): %v\n", i+1, err)
			time.Sleep(1 * time.Second)
			continue
		}
		
		fmt.Printf("Критическая ошибка отправки: %v\n", err)
		return false
	}
	fmt.Println("Не удалось отправить сообщение после 3 попыток")
	return false
}
