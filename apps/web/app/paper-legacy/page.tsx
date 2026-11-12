export default function Home() {
 return <main className="mx-auto max-w-5xl p-10">
 <header className="flex items-center justify-between border-b border-slate-700 pb-6"><h1 className="text-2xl font-bold">BSB Trading Dashboard</h1><span className="rounded-full bg-amber-400/10 px-4 py-2 text-amber-300">PAPER · MOCK</span></header>
 <p className="mt-6 text-slate-400">Task 01 · 모의 자동매매 개발 환경 · 실제 거래 데이터 아님</p>
 <section className="my-8 grid gap-4 sm:grid-cols-3">{[['초기 모의 투자금','₩1,200,000'],['포지션','0 / 2'],['자동매매','미구현 · 비활성']].map(([label,value])=><article key={label} className="rounded-xl border border-slate-700 bg-slate-900 p-6"><p className="text-sm text-slate-400">{label}</p><p className="mt-3 text-2xl font-semibold">{value}</p></article>)}</section>
 <section className="rounded-xl border border-slate-700 p-6"><h2 className="text-lg font-semibold">Alive Coin Ranking</h2><p className="mt-4 text-slate-400">시장 데이터 수집기는 Task 02에서 구현합니다. 현재 수신 데이터가 없습니다.</p></section>
 <p className="mt-8 text-sm text-slate-500">표시 금액은 초기 설정 예시입니다. 계좌·주문·체결 기능과 실제 주문 API는 제공하지 않습니다.</p>
 </main>;
}
