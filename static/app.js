const elements = Object.fromEntries([
    "startup", "login-screen", "login-form", "access-key", "login-button", "login-error",
    "chat-screen", "logout-button", "history-status", "history-error-panel", "history-error",
    "reload-history", "empty-state", "messages", "message-form", "message-input",
    "character-count", "send-button", "message-error", "send-status",
].map((id) => [id, document.getElementById(id)]));

const keyPattern = /^[A-Za-z0-9_-]{8,255}$/;
const state = { loggingIn: false, sending: false, loadingHistory: false, authenticated: false, historyReady: false };
const dateFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });

class ApiError extends Error {
    constructor(message, status) {
        super(message);
        this.status = status;
    }
}

async function request(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 70000);
    try {
        const response = await fetch(path, {
            ...options,
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", ...options.headers },
            signal: controller.signal,
        });
        const data = await response.json().catch(() => null);
        if (!response.ok) {
            throw new ApiError(
                typeof data?.detail === "string" ? data.detail : "Не удалось выполнить запрос. Повторите попытку.",
                response.status,
            );
        }
        if (data === null) {
            throw new ApiError("Получен некорректный ответ сервера. Обновите страницу.", 502);
        }
        return data;
    } catch (error) {
        if (error instanceof ApiError) throw error;
        if (error.name === "AbortError") {
            throw new ApiError("Сервер не успел ответить. Обновите историю перед повторной отправкой.", 0);
        }
        throw new ApiError("Нет связи с сервером. Проверьте соединение и повторите попытку.", 0);
    } finally {
        clearTimeout(timeout);
    }
}

function messageLength() {
    return Array.from(elements["message-input"].value).length;
}

function updateButtons() {
    const count = messageLength();
    elements["login-button"].disabled = state.loggingIn || !keyPattern.test(elements["access-key"].value);
    elements["login-button"].textContent = state.loggingIn ? "Проверяем…" : "Войти";
    elements["access-key"].readOnly = state.loggingIn;
    elements["send-button"].disabled = !state.historyReady || state.sending || count > 500 || !elements["message-input"].value.trim();
    elements["send-button"].textContent = state.sending ? "Ожидание…" : "Отправить";
    elements["message-input"].readOnly = state.sending;
    elements["logout-button"].disabled = state.sending || state.loadingHistory;
    elements["reload-history"].disabled = state.loadingHistory;
    elements["character-count"].textContent = `${count} / 500`;
    elements["character-count"].classList.toggle("over-limit", count > 500);
    elements["send-status"].textContent = state.sending ? "Помощник отвечает…" : "";
}

function showLogin(message = "") {
    state.authenticated = false;
    state.historyReady = false;
    elements["startup"].hidden = true;
    elements["login-screen"].hidden = false;
    elements["chat-screen"].hidden = true;
    elements["login-error"].textContent = message;
    elements["messages"].replaceChildren();
    elements["access-key"].value = "";
    updateButtons();
    elements["access-key"].focus();
}

function renderText(container, text) {
    const parts = text.split(/```(?:[\w+-]*)\n([\s\S]*?)```/g);
    parts.forEach((part, index) => {
        if (!part) return;
        if (index % 2 === 1) {
            const pre = document.createElement("pre");
            const code = document.createElement("code");
            code.textContent = part.replace(/\n$/, "");
            pre.append(code);
            container.append(pre);
        } else {
            const paragraph = document.createElement("p");
            paragraph.textContent = part;
            container.append(paragraph);
        }
    });
}

function appendMessage(message, pending = false) {
    const article = document.createElement("article");
    article.className = pending ? "message pending" : "message";
    article.dataset.role = message.role;
    const header = document.createElement("div");
    header.className = "message-header";
    const author = document.createElement("span");
    author.className = "message-author";
    author.textContent = message.role === "user" ? "Вы" : "Помощник";
    const time = document.createElement("time");
    time.className = "message-time";
    time.dateTime = message.created_at;
    time.textContent = dateFormatter.format(new Date(message.created_at));
    const content = document.createElement("div");
    content.className = "message-text";
    renderText(content, message.text);
    header.append(author, time);
    article.append(header, content);
    elements["messages"].append(article);
    return article;
}

async function loadHistory() {
    state.loadingHistory = true;
    state.historyReady = false;
    elements["history-status"].textContent = "Загрузка истории…";
    elements["history-error-panel"].hidden = true;
    updateButtons();
    try {
        const data = await request("/api/messages");
        elements["messages"].replaceChildren();
        data.messages.forEach((message) => appendMessage(message));
        elements["empty-state"].hidden = data.messages.length > 0;
        state.historyReady = true;
    } catch (error) {
        if (error.status === 401) {
            showLogin(error.message);
        } else {
            elements["history-error"].textContent = error.message;
            elements["history-error-panel"].hidden = false;
        }
    } finally {
        state.loadingHistory = false;
        elements["history-status"].textContent = "";
        updateButtons();
    }
}

async function showChat() {
    state.authenticated = true;
    elements["startup"].hidden = true;
    elements["login-screen"].hidden = true;
    elements["chat-screen"].hidden = false;
    elements["message-error"].textContent = "";
    await loadHistory();
}

elements["access-key"].addEventListener("input", () => {
    const value = elements["access-key"].value;
    const invalid = value !== "" && !keyPattern.test(value);
    elements["login-error"].textContent = invalid ? "Проверьте длину ключа и допустимые символы." : "";
    elements["access-key"].setAttribute("aria-invalid", String(invalid));
    updateButtons();
});

elements["login-form"].addEventListener("submit", async (event) => {
    event.preventDefault();
    if (state.loggingIn || !keyPattern.test(elements["access-key"].value)) return;
    state.loggingIn = true;
    elements["login-error"].textContent = "";
    updateButtons();
    try {
        await request("/api/login", {
            method: "POST",
            body: JSON.stringify({ key: elements["access-key"].value }),
        });
        elements["access-key"].value = "";
        await showChat();
        if (state.authenticated && state.historyReady) elements["message-input"].focus();
    } catch (error) {
        elements["login-error"].textContent = error.message;
    } finally {
        state.loggingIn = false;
        updateButtons();
    }
});

elements["logout-button"].addEventListener("click", async () => {
    if (state.sending || state.loadingHistory) return;
    elements["logout-button"].disabled = true;
    try {
        await request("/api/logout", { method: "POST" });
        elements["message-input"].value = "";
        showLogin();
    } catch (error) {
        elements["message-error"].textContent = error.message;
    } finally {
        updateButtons();
    }
});

elements["message-input"].addEventListener("input", () => {
    const tooLong = messageLength() > 500;
    elements["message-error"].textContent = tooLong ? "Сообщение не должно превышать 500 символов." : "";
    elements["message-input"].setAttribute("aria-invalid", String(tooLong));
    updateButtons();
});

elements["message-input"].addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        elements["message-form"].requestSubmit();
    }
});

elements["message-form"].addEventListener("submit", async (event) => {
    event.preventDefault();
    const draft = elements["message-input"].value;
    if (state.sending || !state.historyReady || !draft.trim() || messageLength() > 500) return;
    state.sending = true;
    elements["message-error"].textContent = "";
    elements["empty-state"].hidden = true;
    updateButtons();
    const createdAt = new Date().toISOString();
    const question = appendMessage({ role: "user", text: draft.trim(), created_at: createdAt }, true);
    const pending = appendMessage({ role: "assistant", text: "Готовит ответ…", created_at: createdAt }, true);
    pending.scrollIntoView({ block: "nearest" });
    try {
        const data = await request("/api/messages", {
            method: "POST",
            body: JSON.stringify({ text: draft }),
        });
        question.remove();
        pending.remove();
        data.messages.forEach((message) => appendMessage(message));
        elements["message-input"].value = "";
        elements["message-form"].scrollIntoView({ block: "nearest" });
    } catch (error) {
        question.remove();
        pending.remove();
        if (error.status === 401) {
            showLogin(error.message);
        } else {
            elements["message-error"].textContent = error.message;
            elements["empty-state"].hidden = elements["messages"].children.length > 0;
            if (error.status === 0) {
                state.historyReady = false;
                elements["history-error"].textContent = "Проверьте историю: запрос мог завершиться на сервере.";
                elements["history-error-panel"].hidden = false;
            }
        }
    } finally {
        state.sending = false;
        updateButtons();
        if (state.authenticated) elements["message-input"].focus();
    }
});

elements["reload-history"].addEventListener("click", loadHistory);

document.querySelectorAll("[data-question]").forEach((button) => {
    button.addEventListener("click", () => {
        if (state.sending) return;
        elements["message-input"].value = button.dataset.question;
        elements["message-input"].dispatchEvent(new Event("input"));
        elements["message-input"].focus();
    });
});

async function initialize() {
    updateButtons();
    try {
        await request("/api/session");
        await showChat();
    } catch (error) {
        showLogin(error.status === 401 ? "" : error.message);
    }
}

initialize();
