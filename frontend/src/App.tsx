import { NavLink, Route, Routes } from "react-router-dom";
import "./App.css";
import InstrumentsPage from "./pages/InstrumentsPage";
import UploadPage from "./pages/UploadPage";
import DataViewPage from "./pages/DataViewPage";
import BatchesPage from "./pages/BatchesPage";
import IndexMembersPage from "./pages/IndexMembersPage";
import MonthlyFundamentalsPage from "./pages/MonthlyFundamentalsPage";
import DataSourcePanel from "./components/DataSourcePanel";

function App() {
  return (
    <div style={{ maxWidth: 960, margin: "0 auto", padding: 24 }}>
      <header style={{ marginBottom: 24 }}>
        <h1>포트폴리오 관리 — 데이터 관리</h1>
        <nav style={{ display: "flex", gap: 16 }}>
          <NavLink to="/">종목 마스터</NavLink>
          <NavLink to="/upload">데이터 업로드</NavLink>
          <NavLink to="/data">데이터 조회</NavLink>
          <NavLink to="/index-members">지수 구성종목</NavLink>
          <NavLink to="/monthly-fundamentals">월간 펀더멘털</NavLink>
          <NavLink to="/batches">배치 관리</NavLink>
        </nav>
        <DataSourcePanel />
      </header>

      <main>
        <Routes>
          <Route path="/" element={<InstrumentsPage />} />
          <Route path="/upload" element={<UploadPage />} />
          <Route path="/data" element={<DataViewPage />} />
          <Route path="/index-members" element={<IndexMembersPage />} />
          <Route path="/monthly-fundamentals" element={<MonthlyFundamentalsPage />} />
          <Route path="/batches" element={<BatchesPage />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
