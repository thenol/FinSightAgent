import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { apiRequest } from "@/lib/api";
import {
  clearToken,
  getToken,
  parseRole,
  parseUsername,
  setToken,
} from "@/lib/auth";
import type { LoginResponse, Role } from "@/types/api";

type AuthContextValue = {
  token: string;
  role: Role | null;
  username: string | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState(getToken());
  const role = useMemo(() => parseRole(token), [token]);
  const username = useMemo(() => parseUsername(token), [token]);

  useEffect(() => {
    const handleAuthExpired = () => {
      clearToken();
      setTokenState("");
    };
    window.addEventListener("finsight:auth-expired", handleAuthExpired);
    return () => window.removeEventListener("finsight:auth-expired", handleAuthExpired);
  }, []);

  const login = useCallback(async (usernameValue: string, password: string) => {
    const data = await apiRequest<LoginResponse>("/api/v1/auth/login", {
      method: "POST",
      token: null,
      body: JSON.stringify({ username: usernameValue, password }),
    });
    setToken(data.access_token);
    setTokenState(data.access_token);
  }, []);

  const logout = useCallback(() => {
    clearToken();
    setTokenState("");
  }, []);

  const value = useMemo(
    () => ({ token, role, username, login, logout }),
    [token, role, username, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("AuthProvider missing");
  return ctx;
}
