import { NavLink, Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import "./App.css";
import InstrumentsPage from "./pages/InstrumentsPage";
import UploadPage from "./pages/UploadPage";
import DataViewPage from "./pages/DataViewPage";
import BatchesPage from "./pages/BatchesPage";
import IndexMembersPage from "./pages/IndexMembersPage";
import MonthlyFundamentalsPage from "./pages/MonthlyFundamentalsPage";
import FundsPage from "./pages/FundsPage";
import IndicesPage from "./pages/IndicesPage";
import DataDashboardPage from "./pages/DataDashboardPage";
import AlgoDashboardPage from "./pages/AlgoDashboardPage";
import DataSourcePanel from "./components/DataSourcePanel";

type Tab = { to: string; end?: boolean; label: string };

// 데이터 관리 안의 구역. 대시보드가 맨 앞이다 — /data 로 들어오면 여기부터 본다.
const DATA_TABS: Tab[] = [
  { to: "/data", end: true, label: "대시보드" },
  { to: "/data/stocks", label: "주식" },
  { to: "/data/etf", label: "ETF" },
  { to: "/data/funds", label: "펀드" },
  { to: "/data/indices", label: "지수" },
  { to: "/data/upload", label: "데이터 업로드" },
  { to: "/data/batches", label: "배치 관리" },
];

// 자산군별 하위 메뉴. 펀드는 테이블 구조가 달라(funds/fund_navs) 한 화면으로 끝난다.
const STOCK_TABS: Tab[] = [
  { to: "/data/stocks", end: true, label: "종목 마스터" },
  { to: "/data/stocks/data", label: "데이터 조회" },
  { to: "/data/stocks/index-members", label: "지수 구성종목" },
  { to: "/data/stocks/monthly-fundamentals", label: "월간 펀더멘털" },
];
const ETF_TABS: Tab[] = [
  { to: "/data/etf", end: true, label: "종목 마스터" },
  { to: "/data/etf/data", label: "데이터 조회" },
];

function Tabs({ tabs, className }: { tabs: Tab[]; className: string }) {
  return (
    <nav className={className}>
      {tabs.map((t) => (
        <NavLink key={t.to} to={t.to} end={t.end}>
          {t.label}
        </NavLink>
      ))}
    </nav>
  );
}

/** 데이터 관리 셸 — 입수 경로 패널은 여기에만 둔다. 알고리즘 화면과는 상관이 없다. */
function DataShell() {
  return (
    <>
      <Tabs tabs={DATA_TABS} className="sectionnav" />
      <DataSourcePanel />
      <Outlet />
    </>
  );
}

function AssetSection({ tabs }: { tabs: Tab[] }) {
  return (
    <>
      <Tabs tabs={tabs} className="subnav" />
      <Outlet />
    </>
  );
}

/** 옛 최상위 경로를 /data 아래로 그대로 옮긴다 — 하위 경로와 쿼리를 잃지 않는다. */
function MoveUnderData() {
  const { pathname, search, hash } = useLocation();
  return <Navigate to={`/data${pathname}${search}${hash}`} replace />;
}

function App() {
  return (
    <div className="app">
      <header>
        <h1>포트폴리오 관리</h1>
        <nav className="mainnav">
          <NavLink to="/data">데이터 관리</NavLink>
          <NavLink to="/algo">알고리즘</NavLink>
        </nav>
      </header>

      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/data" replace />} />

          <Route path="/data" element={<DataShell />}>
            <Route index element={<DataDashboardPage />} />

            <Route path="stocks" element={<AssetSection tabs={STOCK_TABS} />}>
              <Route index element={<InstrumentsPage assetType="stock" />} />
              <Route path="data" element={<DataViewPage />} />
              <Route path="index-members" element={<IndexMembersPage />} />
              <Route path="monthly-fundamentals" element={<MonthlyFundamentalsPage />} />
            </Route>

            <Route path="etf" element={<AssetSection tabs={ETF_TABS} />}>
              <Route index element={<InstrumentsPage assetType="etf" />} />
              <Route path="data" element={<DataViewPage />} />
            </Route>

            <Route path="funds" element={<FundsPage />} />
            <Route path="indices" element={<IndicesPage />} />
            <Route path="upload" element={<UploadPage />} />
            <Route path="batches" element={<BatchesPage />} />
          </Route>

          <Route path="/algo" element={<AlgoDashboardPage />} />

          {/* 옛 경로 — 북마크가 깨지지 않게 새 위치로 보낸다. 하위 경로까지 그대로
              옮기므로 /stocks/data → /data/stocks/data 로 간다. */}
          {["/stocks", "/etf", "/funds", "/indices", "/upload", "/batches"].map((p) => (
            <Route key={p} path={`${p}/*`} element={<MoveUnderData />} />
          ))}
          <Route path="/index-members" element={<Navigate to="/data/stocks/index-members" replace />} />
          <Route path="/monthly-fundamentals"
                 element={<Navigate to="/data/stocks/monthly-fundamentals" replace />} />
        </Routes>
      </main>
    </div>
  );
}

export default App;
