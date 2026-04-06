const DmAuth = (() => {
    const SHARED_AUTH_URL = "https://auth.deutschmark.online";
    const LOCAL_SHARED_AUTH_PROXY_URL = "/api/shared-auth";
    const LOCAL_AUTH_TOKEN_STORAGE_KEY = "dmSharedAuthToken";
    const LOCAL_POST_LOGIN_HASH_STORAGE_KEY = "dmSharedAuthPostLoginHash";
    const SUPPORTED_REMOTE_RETURN_ORIGINS = new Set([
        "https://toolkit.deutschmark.online",
        "https://collab.deutschmark.online",
    ]);

    class SharedAuthError extends Error {
        constructor(message, status = 0, data = null) {
            super(message);
            this.name = "SharedAuthError";
            this.status = status;
            this.data = data;
        }
    }

    let state = {
        error: null,
        isLoading: true,
        user: null,
    };

    function $(id) {
        return document.getElementById(id);
    }

    function normalizeHostname(hostname) {
        return String(hostname || "").replace(/^\[|\]$/g, "").toLowerCase();
    }

    function isLoopbackHostname(hostname) {
        const normalized = normalizeHostname(hostname);
        return normalized === "localhost"
            || normalized === "127.0.0.1"
            || normalized === "0.0.0.0"
            || normalized === "::1";
    }

    function readCookie(name) {
        const cookies = document.cookie.split(/;\s*/);
        for (const cookie of cookies) {
            if (!cookie) continue;
            const [key, ...rest] = cookie.split("=");
            if (key === name) {
                return decodeURIComponent(rest.join("="));
            }
        }
        return null;
    }

    function readStoredAuthToken() {
        try {
            return window.localStorage.getItem(LOCAL_AUTH_TOKEN_STORAGE_KEY) || "";
        } catch (error) {
            return "";
        }
    }

    function readStoredPostLoginHash() {
        try {
            return window.sessionStorage.getItem(LOCAL_POST_LOGIN_HASH_STORAGE_KEY) || "";
        } catch (error) {
            return "";
        }
    }

    function writeStoredAuthToken(token) {
        try {
            if (token) {
                window.localStorage.setItem(LOCAL_AUTH_TOKEN_STORAGE_KEY, token);
            } else {
                window.localStorage.removeItem(LOCAL_AUTH_TOKEN_STORAGE_KEY);
            }
        } catch (error) {
            // Ignore storage failures.
        }
    }

    function writeStoredPostLoginHash(hash) {
        try {
            if (hash) {
                window.sessionStorage.setItem(LOCAL_POST_LOGIN_HASH_STORAGE_KEY, hash);
            } else {
                window.sessionStorage.removeItem(LOCAL_POST_LOGIN_HASH_STORAGE_KEY);
            }
        } catch (error) {
            // Ignore storage failures.
        }
    }

    function getRawCurrentOrigin() {
        return window.location.origin;
    }

    function normalizeLocalOrigin(origin) {
        try {
            const url = new URL(origin);
            if (url.protocol === "http:" && isLoopbackHostname(url.hostname)) {
                url.hostname = "localhost";
            }
            return url.origin;
        } catch (error) {
            return origin;
        }
    }

    function normalizeLocalUrl(url) {
        if (!(url instanceof URL)) {
            return url;
        }
        if (url.protocol === "http:" && isLoopbackHostname(url.hostname)) {
            url.hostname = "localhost";
        }
        return url;
    }

    function getCurrentOrigin() {
        return normalizeLocalOrigin(getRawCurrentOrigin());
    }

    function isLocalhostOrigin(origin) {
        try {
            const url = new URL(normalizeLocalOrigin(origin));
            return url.protocol === "http:" && url.hostname === "localhost";
        } catch (error) {
            return false;
        }
    }

    function isReturnOriginSupported() {
        const origin = getCurrentOrigin();
        return SUPPORTED_REMOTE_RETURN_ORIGINS.has(origin) || isLocalhostOrigin(origin);
    }

    function shouldUseLocalProxy() {
        return isLocalhostOrigin(getCurrentOrigin());
    }

    function getRequestBaseUrl() {
        return shouldUseLocalProxy() ? LOCAL_SHARED_AUTH_PROXY_URL : SHARED_AUTH_URL;
    }

    function buildReturnTo() {
        const currentUrl = normalizeLocalUrl(new URL(window.location.href));
        writeStoredPostLoginHash(currentUrl.hash || "");
        currentUrl.hash = "";
        return currentUrl.toString();
    }

    function buildLoginUrl() {
        if (!isReturnOriginSupported()) {
            throw new Error(
                `Shared Twitch auth is only wired for http://localhost:<port> or an approved deutschmark.online app origin. Current origin: ${getRawCurrentOrigin()}.`,
            );
        }

        const url = new URL(`${SHARED_AUTH_URL}/twitch/auth`);
        url.searchParams.set("returnTo", buildReturnTo());
        return url.toString();
    }

    function readHashAuthToken(hash) {
        const rawHash = String(hash || "");
        if (!rawHash || rawHash === "#") {
            return { cleanHash: "", token: "" };
        }

        const normalized = rawHash.startsWith("#") ? rawHash.slice(1) : rawHash;
        let fragmentPath = normalized;
        let fragmentQuery = "";

        if (normalized.startsWith("?")) {
            fragmentPath = "";
            fragmentQuery = normalized.slice(1);
        } else {
            const queryIndex = normalized.indexOf("?");
            if (queryIndex >= 0) {
                fragmentPath = normalized.slice(0, queryIndex);
                fragmentQuery = normalized.slice(queryIndex + 1);
            } else if (normalized.startsWith("dm_auth_token=")) {
                fragmentPath = "";
                fragmentQuery = normalized;
            }
        }

        if (!fragmentQuery) {
            return { cleanHash: rawHash, token: "" };
        }

        const params = new URLSearchParams(fragmentQuery);
        const token = params.get("dm_auth_token") || "";
        if (!token) {
            return { cleanHash: rawHash, token: "" };
        }

        params.delete("dm_auth_token");
        const remainingQuery = params.toString();
        const cleanHash = fragmentPath
            ? `#${fragmentPath}${remainingQuery ? `?${remainingQuery}` : ""}`
            : (remainingQuery ? `#?${remainingQuery}` : "");

        return { cleanHash, token };
    }

    function consumeAuthTokenFromUrl() {
        const currentUrl = new URL(window.location.href);
        const searchToken = currentUrl.searchParams.get("dm_auth_token") || "";
        const hashAuth = searchToken
            ? { cleanHash: currentUrl.hash || "", token: "" }
            : readHashAuthToken(currentUrl.hash);
        const token = searchToken || hashAuth.token || "";
        if (!token) {
            return readStoredAuthToken();
        }

        writeStoredAuthToken(token);
        currentUrl.searchParams.delete("dm_auth_token");
        if (hashAuth.token) {
            currentUrl.hash = hashAuth.cleanHash;
        }
        if (!currentUrl.hash) {
            const storedHash = readStoredPostLoginHash();
            if (storedHash) {
                currentUrl.hash = storedHash;
            }
        }
        writeStoredPostLoginHash("");
        window.history.replaceState({}, "", currentUrl.toString());
        return token;
    }

    function buildQuery(params = {}) {
        const query = new URLSearchParams();
        Object.entries(params).forEach(([key, value]) => {
            if (value === undefined || value === null || value === "") return;
            query.set(key, String(value));
        });
        const encoded = query.toString();
        return encoded ? `?${encoded}` : "";
    }

    async function request(path, init = {}) {
        const method = String(init.method || "GET").toUpperCase();
        const headers = new Headers(init.headers || {});
        const storedToken = readStoredAuthToken();
        headers.set("Accept", "application/json");

        if (init.body && !headers.has("Content-Type")) {
            headers.set("Content-Type", "application/json");
        }

        if (storedToken && !headers.has("Authorization")) {
            headers.set("Authorization", `Bearer ${storedToken}`);
        }

        if (!["GET", "HEAD"].includes(method)) {
            const csrfToken = readCookie("dm_csrf");
            if (csrfToken) {
                headers.set("X-CSRF-Token", csrfToken);
            }
        }

        let response;
        try {
            response = await fetch(`${getRequestBaseUrl()}${path}`, {
                ...init,
                credentials: "include",
                headers,
            });
        } catch (error) {
            throw new SharedAuthError(
                error instanceof Error ? error.message : "Network request failed",
                0,
                null,
            );
        }

        let data = null;
        try {
            data = await response.json();
        } catch (error) {
            data = null;
        }

        if (!response.ok) {
            if (response.status === 401 && storedToken) {
                writeStoredAuthToken("");
            }
            throw new SharedAuthError(
                response.statusText || "Shared auth request failed",
                response.status,
                data,
            );
        }

        return data;
    }

    function initialsForUser(user) {
        if (!user || !user.name) return "DM";
        return String(user.name)
            .split(/\s+/)
            .slice(0, 2)
            .map((part) => part.charAt(0).toUpperCase())
            .join("") || "DM";
    }

    function describeError(error) {
        if (error instanceof SharedAuthError) {
            if (error.status === 0) {
                return shouldUseLocalProxy()
                    ? "This app could not reach auth.deutschmark.online through the local auth bridge."
                    : "auth.deutschmark.online is unreachable from this app session.";
            }
            if (error.status === 401) {
                return null;
            }
            if (error.status === 403) {
                return `This origin is not allowed by auth.deutschmark.online. Current origin: ${getRawCurrentOrigin()}. Start the app on http://localhost:<port> or an approved deutschmark.online app origin.`;
            }
            if (error.data && typeof error.data === "object" && "error" in error.data) {
                return String(error.data.error || "Shared auth request failed.");
            }
            return error.message || "Shared auth request failed.";
        }

        if (error instanceof Error) {
            return error.message;
        }

        return "Shared auth request failed.";
    }

    function dispatchStateChange() {
        document.dispatchEvent(new CustomEvent("dm:auth-session", {
            detail: {
                error: state.error,
                isLoading: state.isLoading,
                user: state.user,
            },
        }));
    }

    function setState(nextState) {
        state = {
            ...state,
            ...nextState,
        };
        render();
        dispatchStateChange();
    }

    function setAvatar(user) {
        const avatar = $("auth-session-avatar");
        if (!avatar) return;

        if (user && user.avatar) {
            avatar.innerHTML = `<img src="${user.avatar}" alt="${user.name || user.login || "Twitch user"}" referrerpolicy="no-referrer">`;
            avatar.classList.add("has-image");
            return;
        }

        avatar.textContent = initialsForUser(user);
        avatar.classList.remove("has-image");
    }

    function render() {
        const { error, isLoading, user } = state;

        const pillValue = $("auth-pill-value");
        if (pillValue) {
            if (isLoading) pillValue.textContent = "Checking...";
            else if (user?.login) pillValue.textContent = `@${user.login}`;
            else if (error) pillValue.textContent = "Unavailable";
            else pillValue.textContent = "Not connected";
        }

        const sessionName = $("auth-session-name");
        if (sessionName) {
            if (isLoading) sessionName.textContent = "Checking shared auth session…";
            else if (user?.name) sessionName.textContent = `${user.name} (@${user.login})`;
            else sessionName.textContent = "No Twitch session connected";
        }

        const sessionDetail = $("auth-session-detail");
        if (sessionDetail) {
            if (user?.id) {
                sessionDetail.textContent = "Shared Twitch identity is ready to sync into the Video Editor.";
            } else if (error) {
                sessionDetail.textContent = "Shared Twitch auth could not be used from this app session.";
            } else {
                sessionDetail.textContent = "Uses the same auth.deutschmark.online Twitch login as the toolkit.";
            }
        }

        const sessionError = $("auth-session-error");
        if (sessionError) {
            sessionError.textContent = error || "Run the app on http://localhost:<port> or an approved deutschmark.online app origin to use shared auth.";
            sessionError.classList.toggle("error-msg", Boolean(error));
            sessionError.classList.toggle("settings-hint", !error);
        }

        const reelHeading = $("reel-auth-heading");
        if (reelHeading) {
            reelHeading.textContent = user?.login
                ? `Connected as @${user.login}`
                : "Connect your Twitch identity";
        }

        const reelSummary = $("reel-auth-summary");
        if (reelSummary) {
            if (user?.login) {
                reelSummary.textContent = "Use the connected Twitch account to fill the streamer identity on this project.";
            } else if (error) {
                reelSummary.textContent = error;
            } else {
                reelSummary.textContent = "Sign in with the shared auth worker, then sync your Twitch login into this project.";
            }
            reelSummary.classList.toggle("error-msg", Boolean(error));
            reelSummary.classList.toggle("settings-hint", !error);
        }

        setAvatar(user);

        const loginBtn = $("auth-login-btn");
        if (loginBtn) {
            loginBtn.disabled = isLoading;
            loginBtn.textContent = user ? "Switch Twitch Account" : "Login with Twitch";
        }

        const refreshBtn = $("auth-refresh-btn");
        if (refreshBtn) {
            refreshBtn.disabled = isLoading;
        }

        const logoutBtn = $("auth-logout-btn");
        if (logoutBtn) {
            logoutBtn.classList.toggle("hidden", !user);
            logoutBtn.disabled = isLoading;
        }

        const reelSyncBtn = $("reel-auth-sync-btn");
        if (reelSyncBtn) {
            reelSyncBtn.disabled = !user || isLoading;
        }
    }

    async function refreshSession() {
        if (!isReturnOriginSupported()) {
            setState({
                error: `Shared Twitch auth is only wired for http://localhost:<port> or an approved deutschmark.online app origin. Current origin: ${getRawCurrentOrigin()}.`,
                isLoading: false,
                user: null,
            });
            return null;
        }

        setState({
            error: null,
            isLoading: true,
        });

        try {
            const data = await request("/session");
            setState({
                error: null,
                isLoading: false,
                user: data?.user || null,
            });
            return data?.user || null;
        } catch (error) {
            const message = describeError(error);
            setState({
                error: message,
                isLoading: false,
                user: null,
            });
            return null;
        }
    }

    function login() {
        try {
            window.location.assign(buildLoginUrl());
        } catch (error) {
            setState({
                error: describeError(error),
                isLoading: false,
                user: null,
            });
        }
    }

    async function logout() {
        try {
            await request("/logout", { method: "POST" });
        } catch (error) {
            // Clear the local state even if the upstream logout fails.
        } finally {
            writeStoredAuthToken("");
            setState({
                error: null,
                isLoading: false,
                user: null,
            });
        }
    }

    function applyToVideoEditor() {
        if (!state.user) {
            void refreshSession();
            return false;
        }

        if (typeof ReelMaker !== "undefined" && typeof ReelMaker.applyAuthProfile === "function") {
            return ReelMaker.applyAuthProfile(state.user, {
                overwrite: true,
                persist: true,
            });
        }

        return false;
    }

    function fetchTwitchVideos(params = {}) {
        return request(`/twitch/videos${buildQuery(params)}`);
    }

    function fetchTwitchMarkers(videoId, params = {}) {
        if (!videoId) {
            return Promise.reject(new Error("Missing Twitch video ID."));
        }

        return request(`/twitch/markers${buildQuery({
            ...params,
            video_id: videoId,
        })}`);
    }

    function fetchTwitchClips(params = {}) {
        return request(`/twitch/clips${buildQuery(params)}`);
    }

    function fetchEditorSummary() {
        return request("/editor/summary");
    }

    function updateEditorSummary(summary = {}) {
        return request("/editor/summary", {
            method: "PUT",
            body: JSON.stringify(summary),
        });
    }

    function fetchEditorFeed() {
        return request("/editor/feed");
    }

    function createEditorFeed(item = {}) {
        return request("/editor/feed", {
            method: "POST",
            body: JSON.stringify(item),
        });
    }

    function deleteEditorFeed(id) {
        const params = id ? `?id=${encodeURIComponent(id)}` : "";
        return request(`/editor/feed${params}`, {
            method: "DELETE",
        });
    }

    async function init() {
        const token = consumeAuthTokenFromUrl();
        if (token) {
            setState({
                error: null,
                isLoading: true,
                user: null,
            });
        }
        render();
        await refreshSession();
        window.addEventListener("focus", () => {
            void refreshSession();
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", () => {
            void init();
        });
    } else {
        void init();
    }

    return {
        applyToVideoEditor,
        getState() {
            return {
                error: state.error,
                isLoading: state.isLoading,
                user: state.user,
            };
        },
        fetchTwitchMarkers,
        fetchTwitchClips,
        fetchTwitchVideos,
        fetchEditorFeed,
        fetchEditorSummary,
        isReturnOriginSupported,
        login,
        logout,
        createEditorFeed,
        deleteEditorFeed,
        refreshSession,
        updateEditorSummary,
    };
})();
