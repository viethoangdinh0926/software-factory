import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { HomePage } from "./HomePage";
import { SessionErrorBoundary } from "./SessionErrorBoundary";
import { SessionPage } from "./SessionPage";
import "./App.css";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route
          path="/sessions/:sessionId"
          element={
            <SessionErrorBoundary brand="Engineer Agent">
              <SessionPage />
            </SessionErrorBoundary>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
