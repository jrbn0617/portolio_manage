import { NavLink, Navigate, Outlet, Route, Routes } from "react-router-dom";
import "./App.css";
import InstrumentsPage from "./pages/InstrumentsPage";
import UploadPage from "./pages/UploadPage";
import DataViewPage from "./pages/DataViewPage";
import BatchesPage from "./pages/BatchesPage";
import IndexMembersPage from "./pages/IndexMembersPage";
import MonthlyFundamentalsPage from "./pages/MonthlyFundamentalsPage";
import FundsPage from "./pages/FundsPage";
import IndicesPage from "./pages/IndicesPage";
import DataSourcePanel from "./components/DataSourcePanel";

type Tab = { to: string; end?: boolean; label: string };

// 자산군별 하위 메뉴. 펀드는 테이블 구조가 달라(funds/fund_navs) 한 화면으로 끝난다.
const STOCK_TABS: Tab[] = [
  { to: "/stocks", end: true, label: "종목 마스터" },
  { to: "/stocks/data", label: "데이터 조회" },
  { to: "/stocks/index-members", label: "지수 구성종목" },
  { to: "/stocks/monthly-fundamentals", label: "월간 펀더멘털" },
];
const ETF_TABS: Tab[] = [
  { to: "/etf", end: true, label: "종목 마스터" },
  { to: "/etf/data", label: "데이터 조회" },
];

function Section({ tabs }: { tabs: Tab[] }) {
  return (
    <>
      <nav className="subnav">
        {tabs.map((t) => (
          <NavLink key={t.to} to={t.to} end={t.end}>
            {t.label}
          </NavLink>
        ))}
      </nav>
      <Outlet />
    </>
  );
}

function App() {
  return (
    <div style={{ maxWidth: 1180, margin: "0 auto", padding: 24 }}>
      <header style={{ marginBottom: 20 }}>
        <h1 style={{ marginBottom: 12 }}>포트폴리오 관리 — 데이터 관리</h1>
        <nav className="mainnav">
          <NavLink to="/stocks">주식</NavLink>
          <NavLink to="/etf">ETF</NavLink>
          <NavLink to="/funds">펀드</NavLink>
          <NavLink to="/indices">지수</NavLink>
          <span className="navgap" />
          <NavLink to="/upload">데이터 업로드</NavLink>
          <NavLink to="/batches">배치 관리</NavLink>
        </nav>
        <DataSourcePanel />
      </header>

      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/stocks" replace />} />

          <Route path="/stocks" element={<Section tabs={STOCK_TABS} />}>
            <Route index element={<InstrumentsPage assetType="stock" />} />
            <Route path="data" element={<DataViewPage />} />
            <Route path="index-members" element={<IndexMembersPage />} />
            <Route path="monthly-fundamentals" element={<MonthlyFundamentalsPage />} />
          </Route>

          <Route path="/etf" element={<Section tabs={ETF_TABS} />}>
            <Route index element={<InstrumentsPage assetType="etf" />} />
            <Route path="data" element={<DataViewPage />} />
          </Route>

          <Route path="/funds" element={<FundsPage />} />
          <Route path="/indices" element={<IndicesPage />} />
          <Route path="/upload" element={<UploadPage />} />
          <Route path="/batches" element={<BatchesPage />} />

          {/* 옛 경로 — 북마크가 깨지지 않게 새 위치로 보낸다 */}
          <Route path="/data" element={<Navigate to="/stocks/data" replace />} />
          <Route path="/index-members" element={<Navigate to="/stocks/index-members" replace />} />
          <Route path="/monthly-fundamentals"
                 element={<Navigate to="/stocks/monthly-fundamentals" replace />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
