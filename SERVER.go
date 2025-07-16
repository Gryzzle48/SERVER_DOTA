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
	FoundCount int // ДОБАВЛЕНО НУЖНОЕ ПОЛЕ
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
	defer conn.Close()

	client := &Client{
		Conn: conn,
	}

	scanner := bufio.NewScanner(conn)
	for scanner.Scan() {
		msg := scanner.Text()
		parts := strings.Split(msg, ":")
		if len(parts) < 3 {
			sendResponse(conn, "ERROR:INVALID_FORMAT")
			continue
		}

		cmd, sessionID, clientID := parts[0], parts[1], parts[2]
		client.ID = clientID

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
		fmt.Println("Ошибка чтения:", err)
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
			sendResponse(readyClients[i].Conn, "START_SEARCH")
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
                sendResponse(c.Conn, "ACCEPT_MATCH")
                // Сбрасываем статус
                c.Found = false
            }
        }
        // Сбрасываем счетчик
        session.FoundCount = 0
        
        // Останавливаем и сбрасываем таймер если активен
        if session.Timer != nil {
            session.Timer.Stop()
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
            sendResponse(c.Conn, "DECLINE_MATCH")
            c.Found = false
        }
    }
    
    // Сбрасываем счетчик и таймер
    session.FoundCount = 0
    session.Timer = nil
}

func sendResponse(conn net.Conn, msg string) {
	_, err := fmt.Fprintln(conn, msg)
	if err != nil {
		fmt.Println("Ошибка отправки:", err)
	}
}
