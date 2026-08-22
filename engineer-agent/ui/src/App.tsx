import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { HomePage } from "./HomePage";
import { SessionPage } from "./SessionPage";
import "./App.css";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/sessions/:sessionId" element={<SessionPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
