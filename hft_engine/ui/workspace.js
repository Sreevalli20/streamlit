(() => {
  "use strict";

  const root = document.getElementById("workspace-main");
  const nav = document.getElementById("workspace-nav");
  const enginePage = document.getElementById("engine-dashboard");
  const views = new Set([
    "dashboard", "markets", "stocks", "watchlist", "trading", "orders",
    "portfolio", "analytics", "system", "settings"
  ]);
  const defaultWatchlist = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK"];
  const alertStorageKey = "chronosmatch-price-alerts";
  const refreshStorageKey = "chronosmatch-refresh-interval";
  let activeView = "dashboard";
  let selectedSymbol = "RELIANCE";
  let selectedRange = "1D";
  let market = null;
  let paper = null;
  let detail = null;
  let notice = "";
  let noticeIsError = false;
  let exchangeFilter = "all";
  let sectorFilter = "all";
  let sortBy = "change_percent";
  let orderFilter = "all";
  let stockSearch = "";
  let priceAlerts = readPriceAlerts();
  let refreshInterval = Number(localStorage.getItem(refreshStorageKey)) || 2000;
  if (![1000, 2000, 5000, 10000].includes(refreshInterval)) refreshInterval = 2000;
  let refreshTimer = null;

  function readList(key, fallback) {
    try {
      const value = JSON.parse(localStorage.getItem(key) || "null");
      return Array.isArray(value) ? value.filter(item => typeof item === "string") : fallback;
    } catch {
      return fallback;
    }
  }

  function readPriceAlerts() {
    try {
      const value = JSON.parse(localStorage.getItem(alertStorageKey) || "[]");
      if (!Array.isArray(value)) return [];
      return value.filter(alert =>
        alert && typeof alert.id === "string" && typeof alert.symbol === "string"
        && ["above", "below"].includes(alert.condition)
        && Number.isFinite(alert.target) && typeof alert.triggered === "boolean"
      );
    } catch {
      return [];
    }
  }

  let watchlist = readList("chronosmatch-watchlist", defaultWatchlist);
  let recentlyViewed = readList("chronosmatch-recent", []);

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, char => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[char]);
  }

  function money(value, currency = "INR") {
    if (!Number.isFinite(Number(value))) return "—";
    return `${currency === "INR" ? "₹" : currency + " "}${Number(value).toLocaleString("en-IN", {
      minimumFractionDigits: 2, maximumFractionDigits: 2
    })}`;
  }

  function number(value) {
    return Number(value || 0).toLocaleString("en-IN", { maximumFractionDigits: 2 });
  }

  function signClass(value) {
    return Number(value) >= 0 ? "positive" : "negative";
  }

  async function getJson(url) {
    try {
      const response = await fetch(url, { cache: "no-store" });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
      return result;
    } catch (error) {
      throw connectionError(error);
    }
  }

  async function postJson(url, payload = {}) {
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
      return result;
    } catch (error) {
      throw connectionError(error);
    }
  }

  function connectionError(error) {
    if (error instanceof TypeError && /fetch/i.test(error.message)) {
      return new Error("ChronosMatch is not reachable. Start the local server and reload this page.");
    }
    return error;
  }

  function showNotice(message, isError = false) {
    notice = message;
    noticeIsError = isError;
    const element = document.getElementById("workspace-notice");
    if (element) {
      element.textContent = message;
      element.classList.toggle("error", isError);
    }
  }

  function pageHeader(title, description) {
    return `<div class="page-heading"><div><h1>${title}</h1><p>${description}</p></div></div>`;
  }

  function panel(title, content) {
    return `<section class="workspace-panel"><h2>${title}</h2>${content}</section>`;
  }

  function cards(items) {
    return `<div class="workspace-grid">${items.map(item =>
      `<div class="workspace-card"><div class="label">${item.label}</div>
       <div class="value ${item.className || ""}">${item.value}</div>
       ${item.note ? `<div class="muted">${item.note}</div>` : ""}</div>`
    ).join("")}</div>`;
  }

  function stockTable(instruments, showWatchAction = true) {
    if (!instruments.length) {
      return `<div class="empty-state">No stocks match. Try another company name, symbol, sector, or exchange.</div>`;
    }
    return `<div class="workspace-table-wrap"><table class="workspace-table">
      <thead><tr><th>Company / symbol</th><th>Price</th><th>Change</th><th>Volume</th><th>Sector</th>${showWatchAction ? "<th>Watchlist</th>" : ""}</tr></thead>
      <tbody>${instruments.map(stock => `<tr data-symbol="${escapeHtml(stock.symbol)}">
        <td><strong>${escapeHtml(stock.name)}</strong><br><span class="muted">${escapeHtml(stock.symbol)} · ${escapeHtml(stock.exchange.replace(" (simulated)", ""))}</span></td>
        <td>${money(stock.price)}</td>
        <td class="${signClass(stock.change_percent)}">${stock.change >= 0 ? "+" : ""}${money(stock.change)} (${stock.change_percent >= 0 ? "+" : ""}${Number(stock.change_percent).toFixed(2)}%)</td>
        <td>${number(stock.volume)}</td><td>${escapeHtml(stock.sector)}</td>
        ${showWatchAction ? `<td><button class="workspace-button" data-watch="${escapeHtml(stock.symbol)}">${watchlist.includes(stock.symbol) ? "Remove" : "Add"}</button></td>` : ""}
      </tr>`).join("")}</tbody></table></div>`;
  }

  function instrumentControls() {
    return `<div class="workspace-toolbar">
      <label class="form-field">Search company or symbol
        <input class="workspace-input" id="stock-search" type="search" value="${escapeHtml(stockSearch)}" placeholder="e.g. Infosys or INFY" autocomplete="off">
      </label>
      <label class="form-field">Exchange
        <select class="workspace-select" id="exchange-filter"><option value="all" ${exchangeFilter === "all" ? "selected" : ""}>All available</option><option value="NSE (simulated)" ${exchangeFilter === "NSE (simulated)" ? "selected" : ""}>NSE</option></select>
      </label>
      <label class="form-field">Sector
        <select class="workspace-select" id="sector-filter"><option value="all" ${sectorFilter === "all" ? "selected" : ""}>All sectors</option>${[...new Set((market?.instruments || []).map(s => s.sector))].sort().map(sector => `<option ${sectorFilter === sector ? "selected" : ""}>${escapeHtml(sector)}</option>`).join("")}</select>
      </label>
      <label class="form-field">Sort by
        <select class="workspace-select" id="stock-sort">
          <option value="change_percent" ${sortBy === "change_percent" ? "selected" : ""}>Change</option><option value="price" ${sortBy === "price" ? "selected" : ""}>Price</option><option value="volume" ${sortBy === "volume" ? "selected" : ""}>Volume</option><option value="name" ${sortBy === "name" ? "selected" : ""}>Company</option>
        </select>
      </label>
    </div>`;
  }

  function filteredStocks(source = market?.instruments || []) {
    let rows = source.filter(stock =>
      (exchangeFilter === "all" || stock.exchange === exchangeFilter)
      && (sectorFilter === "all" || stock.sector === sectorFilter)
      && (!stockSearch || `${stock.name} ${stock.symbol}`.toLocaleLowerCase().includes(stockSearch))
    );
    rows = [...rows];
    rows.sort((a, b) => {
      if (sortBy === "name") return a.name.localeCompare(b.name);
      return Number(b[sortBy]) - Number(a[sortBy]);
    });
    return rows;
  }

  function watchlistStocks() {
    return watchlist.map(symbol => market?.instruments.find(stock => stock.symbol === symbol))
      .filter(Boolean);
  }

  function marketDashboard() {
    const instruments = market?.instruments || [];
    const gainers = market?.gainers || [];
    const losers = market?.losers || [];
    const active = market?.most_active || [];
    const holdings = paper?.holdings || [];
    const advancing = instruments.filter(stock => stock.change_percent > 0).length;
    const declining = instruments.filter(stock => stock.change_percent < 0).length;
    const unchanged = instruments.length - advancing - declining;
    return `${pageHeader("Market dashboard", "Market overview and paper portfolio.")}
      ${cards([
        { label: "Paper cash", value: money(paper?.buying_power), note: "Available funds" },
        { label: "Portfolio value", value: money(paper?.portfolio_value), note: "Cash plus holdings" },
        { label: "Paper profit / loss", value: money(paper?.total_pnl), className: signClass(paper?.total_pnl), note: "Unrealized and realized" },
        { label: "Watchlist", value: `${watchlist.length} stocks`, note: "Saved in this browser" },
        { label: "Market breadth", value: `${advancing} up · ${declining} down`, note: `${unchanged} unchanged` },
        { label: "Price alerts", value: `${priceAlerts.filter(alert => !alert.triggered).length} waiting`, note: `${priceAlerts.filter(alert => alert.triggered).length} triggered` }
      ])}
      <div class="workspace-columns">
        ${panel("Top movers", stockTable(gainers.slice(0, 5), false))}
        ${panel("Top decliners", stockTable(losers.slice(0, 5), false))}
        ${panel("Most active", stockTable(active.slice(0, 5), false))}
      </div>
      ${panel("Your holdings", holdings.length ? stockTable(holdings.map(item => ({
        ...item, price: item.market_price, change: item.unrealized_pnl,
        change_percent: item.average_price ? item.unrealized_pnl / (item.average_price * item.quantity) * 100 : 0,
        volume: item.quantity, sector: "Paper holding", exchange: "NSE"
      })), false) : `<div class="empty-state">No paper holdings yet. Place a buy order to get started.</div>`)}
      ${panel("Price alerts", priceAlertsTable())}`;
  }

  function marketPage() {
    return `${pageHeader("Markets", "Browse instruments and manage your watchlist.")}
      ${panel("Stock discovery", `${instrumentControls()}<div id="stock-list">${stockTable(filteredStocks())}</div>`)}`;
  }

  function chartSvg(points) {
    if (!points.length) return `<div class="empty-state">No chart data yet for this session.</div>`;
    const values = points.map(point => point.price);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = max - min || 1;
    const coords = points.map((point, index) => {
      const x = points.length === 1 ? 50 : 2 + index * 96 / (points.length - 1);
      const y = 90 - ((point.price - min) / range) * 80;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    }).join(" ");
    return `<svg class="chart-svg" viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="Price history">
      <polyline points="${coords}" fill="none" stroke="#00f2fe" stroke-width="1.5" vector-effect="non-scaling-stroke"/>
      <text x="2" y="12" fill="#94a3b8" font-size="4">${money(max)}</text>
      <text x="2" y="98" fill="#94a3b8" font-size="4">${money(min)}</text></svg>`;
  }

  function volumeChartSvg(points) {
    if (!points.length) return `<div class="empty-state">No volume data yet for this session.</div>`;
    const samples = points.slice(-80);
    const volumes = samples.map((point, index) =>
      index ? Math.max(0, point.volume - samples[index - 1].volume) : point.volume
    );
    const maximum = Math.max(1, ...volumes);
    const barWidth = 100 / samples.length;
    return `<svg class="chart-svg" viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="Volume history">
      ${volumes.map((volume, index) => {
        const height = Math.max(1, volume / maximum * 86);
        return `<rect x="${(index * barWidth).toFixed(2)}" y="${(94 - height).toFixed(2)}" width="${Math.max(.4, barWidth - .2).toFixed(2)}" height="${height.toFixed(2)}" fill="#4facfe" opacity=".8"/>`;
      }).join("")}</svg>`;
  }

  function stockDetailsPage() {
    const quote = detail?.quote;
    if (!quote) return `${pageHeader("Stocks", "Select an instrument to view details.")}${marketPage()}`;
    const history = detail.history || [];
    const book = detail.book || { bids: [], asks: [], best_bid: null, best_ask: null, spread: null };
    const qtyHeld = paper?.holdings.find(item => item.symbol === quote.symbol)?.quantity || 0;
    return `    ${pageHeader(`${escapeHtml(quote.name)} · ${escapeHtml(quote.symbol)}`, `${escapeHtml(quote.exchange.replace(" (simulated)", ""))} · ${escapeHtml(quote.sector)} · ${escapeHtml(quote.currency)}`)}
    <div class="muted">Prices are simulated.</div>
      ${cards([
        { label: "Price", value: money(quote.price), note: quote.market_status },
        { label: "Change", value: `${quote.change >= 0 ? "+" : ""}${money(quote.change)} (${quote.change_percent >= 0 ? "+" : ""}${quote.change_percent.toFixed(2)}%)`, className: signClass(quote.change_percent) },
        { label: "Day range", value: `${money(quote.low)} – ${money(quote.high)}`, note: `Open ${money(quote.open)}` },
        { label: "Volume", value: number(quote.volume), note: `Previous close ${money(quote.previous_close)}` }
      ])}
      <div class="workspace-columns">
        ${panel("Session charts", `<div class="range-tabs">${["1D","1W","1M","3M","6M","1Y"].map(range => `<button data-range="${range}" class="${range === selectedRange ? "active" : ""}">${range}</button>`).join("")}</div><h3>Price</h3>${chartSvg(history)}<h3>Volume</h3>${volumeChartSvg(history)}`)}
        ${panel("Paper order book", `<div class="detail-grid">${[
          ["Best bid", money(book.best_bid)], ["Best ask", money(book.best_ask)],
          ["Spread", money(book.spread)], ["Your shares", number(qtyHeld)]
        ].map(item => `<div class="detail-item"><span>${item[0]}</span><strong>${item[1]}</strong></div>`).join("")}</div>
        <div class="workspace-table-wrap"><table class="workspace-table"><thead><tr><th>Level</th><th>Bid quantity</th><th>Bid price</th><th>Ask price</th><th>Ask quantity</th></tr></thead><tbody>
        ${Array.from({length:Math.max(book.bids.length,book.asks.length)},(_,index)=>`<tr><td>L${index+1}</td><td class="positive">${number(book.bids[index]?.[1])}</td><td class="positive">${money(book.bids[index]?.[0])}</td><td class="negative">${money(book.asks[index]?.[0])}</td><td class="negative">${number(book.asks[index]?.[1])}</td></tr>`).join("")}
        </tbody></table></div>`)}
      </div>
      <div class="workspace-toolbar"><button class="workspace-button" data-watch="${quote.symbol}">${watchlist.includes(quote.symbol) ? "Remove from watchlist" : "Add to watchlist"}</button>
      <button class="workspace-button buy" data-trade="BUY">Buy paper</button><button class="workspace-button sell" data-trade="SELL">Sell paper</button></div>
      ${panel("Create price alert", `<form id="stock-alert-form" class="workspace-toolbar">
        <label class="form-field">Alert when<select class="workspace-select" name="condition"><option value="above">Price reaches or exceeds</option><option value="below">Price reaches or falls below</option></select></label>
        <label class="form-field">Target price<input class="workspace-input" name="target" type="number" min="0.01" step="0.01" value="${(quote.price * 1.02).toFixed(2)}" required></label>
        <button class="workspace-button primary" type="submit">Save alert</button>
      </form>`)}`;
  }

  function tradingPage() {
    const quote = market?.instruments.find(item => item.symbol === selectedSymbol);
    return `${pageHeader("Paper trading", "Place orders and track your portfolio.")}
      ${cards([{label:"Available buying power",value:money(paper?.buying_power)},{label:"Simulated cash",value:money(paper?.cash)},{label:"Mode",value:"Paper only"},{label:"Selected instrument",value:`<span id="selected-instrument">${escapeHtml(selectedSymbol)}</span>`}])}
      ${panel("Place an order", `<form id="paper-order-form" class="form-grid">
        <label class="form-field">Stock<select class="workspace-select" name="symbol">${(market?.instruments || []).map(stock => `<option value="${stock.symbol}" ${stock.symbol === selectedSymbol ? "selected" : ""}>${escapeHtml(stock.symbol)} — ${escapeHtml(stock.name)}</option>`).join("")}</select></label>
        <label class="form-field">Side<select class="workspace-select" name="side"><option>BUY</option><option>SELL</option></select></label>
        <label class="form-field">Order type<select class="workspace-select" name="type"><option>LIMIT</option><option>MARKET</option></select></label>
        <label class="form-field">Quantity<input class="workspace-input" name="quantity" type="number" min="1" max="1000000" step="1" value="10" required><button class="workspace-button" id="use-max-quantity" type="button">Use maximum available</button></label>
        <label class="form-field" id="limit-price-field">Limit price (INR)<input class="workspace-input" name="price" type="number" min="0.01" step="0.01" value="${quote ? quote.price.toFixed(2) : ""}" required></label>
        <div class="form-field"><span id="order-estimate-label">Estimated value</span><strong id="order-estimate">${money((quote?.price || 0) * 10)}</strong></div>
        <div class="form-field"><span id="order-available"></span><span class="negative" id="order-risk" role="alert"></span></div>
        <div class="form-row"><button type="submit" class="workspace-button primary" id="submit-paper-order">Review & submit paper order</button></div>
      </form>`)}`;
  }

  function ordersPage() {
    const allOrders = paper?.orders || [];
    const orders = orderFilter === "all" ? allOrders : allOrders.filter(order =>
      orderFilter === "open"
        ? ["open", "partially_filled"].includes(order.status) && order.remaining_quantity > 0
        : !["open", "partially_filled"].includes(order.status) || !order.remaining_quantity
    );
    const body = orders.length ? `<div class="workspace-table-wrap"><table class="workspace-table"><thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Type</th><th>Qty / filled</th><th>Limit</th><th>Status</th><th>Action</th></tr></thead><tbody>
      ${orders.map(order => `<tr><td>${escapeHtml(order.created_at)}</td><td>${escapeHtml(order.symbol)}</td><td class="${order.side === "BUY" ? "positive" : "negative"}">${order.side}</td><td>${order.type}</td><td>${number(order.quantity)} / ${number(order.filled_quantity)}</td><td>${money(order.price)}</td><td>${escapeHtml(order.status)}</td><td>${["open","partially_filled"].includes(order.status) && order.remaining_quantity ? `<button class="workspace-button" data-cancel="${order.order_id}">Cancel</button>` : "—"}</td></tr>`).join("")}
      </tbody></table></div>` : `<div class="empty-state">No ${orderFilter === "all" ? "" : `${orderFilter} `}orders in this view.</div>`;
    const openCount = allOrders.filter(order => ["open", "partially_filled"].includes(order.status) && order.remaining_quantity).length;
    return `${pageHeader("Orders", "Track paper orders, fills, and cancellations for this server session.")}${panel("Order history", `<div class="workspace-toolbar"><label class="form-field">Show<select class="workspace-select" id="order-filter"><option value="all" ${orderFilter === "all" ? "selected" : ""}>All orders</option><option value="open" ${orderFilter === "open" ? "selected" : ""}>Open orders</option><option value="closed" ${orderFilter === "closed" ? "selected" : ""}>Completed orders</option></select></label><button class="workspace-button sell" id="cancel-all-orders" ${openCount ? "" : "disabled"}>Cancel all open (${openCount})</button></div>${body}`)}${panel("Recent executions", executionTable())}`;
  }

  function executionTable(symbol = "") {
    const executions = (paper?.executions || []).filter(fill => !symbol || fill.symbol === symbol);
    if (!executions.length) return `<div class="empty-state">No executions yet.</div>`;
    return `<div class="workspace-table-wrap"><table class="workspace-table"><thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Quantity</th><th>Price</th><th>Value</th></tr></thead><tbody>${executions.map(fill =>
      `<tr><td>${escapeHtml(fill.time)}</td><td>${escapeHtml(fill.symbol)}</td><td>${fill.side}</td><td>${number(fill.quantity)}</td><td>${money(fill.price)}</td><td>${money(fill.value)}</td></tr>`
    ).join("")}</tbody></table></div>`;
  }

  function portfolioPage() {
    const holdings = paper?.holdings || [];
    const portfolioValue = Number(paper?.portfolio_value) || 0;
    const content = holdings.length ? `<div class="workspace-table-wrap"><table class="workspace-table"><thead><tr><th>Company</th><th>Shares</th><th>Available</th><th>Avg. cost</th><th>Market price</th><th>Market value</th><th>Unrealized P/L</th><th></th></tr></thead><tbody>${holdings.map(item =>
      `<tr data-symbol="${escapeHtml(item.symbol)}"><td>${escapeHtml(item.name)}<br><span class="muted">${escapeHtml(item.symbol)}</span></td><td>${number(item.quantity)}</td><td>${number(item.available_quantity)}</td><td>${money(item.average_price)}</td><td>${money(item.market_price)}</td><td>${money(item.market_value)}</td><td class="${signClass(item.unrealized_pnl)}">${money(item.unrealized_pnl)}</td><td><button class="workspace-button sell" data-sell-symbol="${escapeHtml(item.symbol)}" ${item.available_quantity ? "" : "disabled"}>Sell</button></td></tr>`
    ).join("")}</tbody></table></div>` : `<div class="empty-state">No holdings yet. Complete a paper buy order to create a holding.</div>`;
    const allocation = holdings.length ? `<div class="workspace-table-wrap"><table class="workspace-table"><thead><tr><th>Asset</th><th>Portfolio allocation</th><th>Value</th></tr></thead><tbody>${holdings.map(item => {
      const percent = portfolioValue > 0 ? item.market_value / portfolioValue * 100 : 0;
      return `<tr><td>${escapeHtml(item.symbol)}</td><td><progress max="100" value="${percent.toFixed(2)}"></progress> ${percent.toFixed(1)}%</td><td>${money(item.market_value)}</td></tr>`;
    }).join("")}</tbody></table></div>` : `<div class="empty-state">Allocation will appear after your first fill.</div>`;
    return `${pageHeader("Portfolio", "Holdings and profit/loss for your paper account.")}
      ${cards([{label:"Cash",value:money(paper?.cash)},{label:"Buying power",value:money(paper?.buying_power)},{label:"Portfolio value",value:money(paper?.portfolio_value)},{label:"Total paper P/L",value:money(paper?.total_pnl),className:signClass(paper?.total_pnl)}])}
      ${panel("Holdings", content)}${panel("Portfolio allocation", allocation)}${panel("P/L breakdown", `<div class="detail-grid"><div class="detail-item"><span>Invested cost</span><strong>${money(paper?.invested_cost)}</strong></div><div class="detail-item"><span>Realized P/L</span><strong class="${signClass(paper?.realized_pnl)}">${money(paper?.realized_pnl)}</strong></div><div class="detail-item"><span>Unrealized P/L</span><strong class="${signClass(paper?.unrealized_pnl)}">${money(paper?.unrealized_pnl)}</strong></div></div>`)}`;
  }

  function analyticsPage() {
    const orders = paper?.orders || [];
    const fills = paper?.executions || [];
    const statusCount = status => orders.filter(order => order.status === status).length;
    const buyCount = orders.filter(order => order.side === "BUY").length;
    const sellCount = orders.filter(order => order.side === "SELL").length;
    const submittedQuantity = orders.reduce((sum, order) => sum + order.quantity, 0);
    const filledQuantity = orders.reduce((sum, order) => sum + order.filled_quantity, 0);
    const buyValue = fills.filter(fill => fill.side === "BUY").reduce((sum, fill) => sum + fill.value, 0);
    const sellValue = fills.filter(fill => fill.side === "SELL").reduce((sum, fill) => sum + fill.value, 0);
    const bySymbol = new Map();
    for (const fill of fills) {
      const summary = bySymbol.get(fill.symbol) || { quantity: 0, value: 0, fills: 0 };
      summary.quantity += fill.quantity;
      summary.value += fill.value;
      summary.fills += 1;
      bySymbol.set(fill.symbol, summary);
    }
    const symbolRows = [...bySymbol.entries()].sort((a, b) => b[1].value - a[1].value);
    const symbolTable = symbolRows.length ? `<div class="workspace-table-wrap"><table class="workspace-table"><thead><tr><th>Symbol</th><th>Executions</th><th>Shares</th><th>Gross value</th><th>Average execution price</th></tr></thead><tbody>${symbolRows.map(([symbol, summary]) => `<tr><td>${escapeHtml(symbol)}</td><td>${number(summary.fills)}</td><td>${number(summary.quantity)}</td><td>${money(summary.value)}</td><td>${money(summary.value / summary.quantity)}</td></tr>`).join("")}</tbody></table></div>` : `<div class="empty-state">Per-symbol execution statistics appear after the first fill.</div>`;
    return `${pageHeader("Analytics", "Session totals calculated from actual paper orders and matching-engine executions.")}
      ${cards([
        {label:"Orders submitted",value:number(orders.length)},
        {label:"Filled",value:number(statusCount("filled"))},
        {label:"Open",value:number(statusCount("open") + statusCount("partially_filled"))},
        {label:"Cancelled / rejected",value:number(statusCount("cancelled") + statusCount("rejected"))},
        {label:"Execution records",value:number(fills.length)},
        {label:"Executed quantity",value:number(fills.reduce((sum, fill) => sum + fill.quantity, 0))},
        {label:"Executed value",value:money(fills.reduce((sum, fill) => sum + fill.value, 0))},
        {label:"Buy / sell orders",value:`${number(buyCount)} / ${number(sellCount)}`},
        {label:"Order fill ratio",value:`${submittedQuantity ? (filledQuantity / submittedQuantity * 100).toFixed(1) : "0.0"}%`,note:"Filled shares / submitted shares"},
        {label:"Buy / sell value",value:`${money(buyValue)} / ${money(sellValue)}`,note:"Executed value"}
      ])}${panel("By-symbol activity", symbolTable)}${panel("Execution history", executionTable())}`;
  }

  function watchlistPage() {
    const rows = watchlistStocks();
    return `${pageHeader("Watchlist", "Your saved symbols are stored in this browser.")}
      ${panel("Your stocks", rows.length ? stockTable(rows) : `<div class="empty-state">Your watchlist is empty. Browse Markets and add a stock to follow it here.</div>`)}
      ${panel("Price alerts", priceAlertsTable())}
      ${recentlyViewed.length ? panel("Recently viewed", stockTable(recentlyViewed.map(symbol => market?.instruments.find(item => item.symbol === symbol)).filter(Boolean), false)) : ""}`;
  }

  function settingsPage() {
    return `${pageHeader("Settings", "Manage your watchlist and paper account.")}
      ${panel("Market data", `<p class="muted">Simulated prices; no broker connection.</p>`)}
      ${panel("Price alerts", `<form id="settings-alert-form" class="workspace-toolbar">
        <label class="form-field">Stock<select class="workspace-select" name="symbol">${(market?.instruments || []).map(stock => `<option value="${escapeHtml(stock.symbol)}">${escapeHtml(stock.symbol)} — ${escapeHtml(stock.name)}</option>`).join("")}</select></label>
        <label class="form-field">Condition<select class="workspace-select" name="condition"><option value="above">At or above</option><option value="below">At or below</option></select></label>
        <label class="form-field">Target price<input class="workspace-input" name="target" type="number" min="0.01" step="0.01" required></label>
        <button class="workspace-button primary" type="submit">Add alert</button>
      </form>${priceAlertsTable()}`)}
      ${panel("Refresh rate", `<label class="form-field">Update market and account every<select class="workspace-select" id="refresh-interval"><option value="1000" ${refreshInterval === 1000 ? "selected" : ""}>1 second</option><option value="2000" ${refreshInterval === 2000 ? "selected" : ""}>2 seconds</option><option value="5000" ${refreshInterval === 5000 ? "selected" : ""}>5 seconds</option><option value="10000" ${refreshInterval === 10000 ? "selected" : ""}>10 seconds</option></select></label>`) }
      ${panel("Paper account", `<p>Starting cash: ${money(1_000_000)}. Account records reset when the server stops.</p><div class="workspace-toolbar"><button class="workspace-button primary" id="export-account">Export orders and executions</button><button class="workspace-button sell" id="reset-paper-account">Reset paper account</button><button class="workspace-button" id="clear-watchlist">Restore default watchlist</button><button class="workspace-button" id="clear-recent">Clear recently viewed</button></div>`) }`;
  }

  function render() {
    nav.querySelectorAll("button[data-view]").forEach(button => {
      button.classList.toggle("active", button.dataset.view === activeView);
      button.setAttribute("aria-current", button.dataset.view === activeView ? "page" : "false");
    });
    document.body.dataset.view = activeView;
    const isSystem = activeView === "system";
    enginePage.classList.toggle("app-hidden", !isSystem);
    root.classList.toggle("app-hidden", isSystem);
    if (isSystem) return;

    const savedForms = new Map();
    root.querySelectorAll("form[id]").forEach(form => {
      const values = {};
      for (const control of form.elements) {
        if (control.name && control.type !== "submit" && control.type !== "button") {
          values[control.name] = control.value;
        }
      }
      savedForms.set(form.id, values);
    });
    const noticeMarkup = `<div class="notice ${noticeIsError ? "error" : ""}" id="workspace-notice">${escapeHtml(notice)}</div>`;
    const pages = {
      dashboard: marketDashboard,
      markets: marketPage,
      stocks: stockDetailsPage,
      watchlist: watchlistPage,
      trading: tradingPage,
      orders: ordersPage,
      portfolio: portfolioPage,
      analytics: analyticsPage,
      settings: settingsPage
    };
    root.innerHTML = `<div class="workspace-page">${pages[activeView]()}${noticeMarkup}</div>`;
    for (const form of root.querySelectorAll("form[id]")) {
      const values = savedForms.get(form.id);
      if (!values) continue;
      for (const [name, value] of Object.entries(values)) {
        if (form.elements.namedItem(name)) form.elements.namedItem(name).value = value;
      }
    }
    wirePageControls();
  }

  function saveWatchlist() {
    try {
      localStorage.setItem("chronosmatch-watchlist", JSON.stringify(watchlist));
    } catch {
      showNotice("Could not save your watchlist in this browser.", true);
    }
  }

  function savePriceAlerts() {
    try {
      localStorage.setItem(alertStorageKey, JSON.stringify(priceAlerts));
    } catch (error) {
      throw new Error(`Could not save price alerts in this browser: ${error.message}`);
    }
  }

  function addPriceAlert(symbol, condition, target) {
    const quote = market?.instruments.find(item => item.symbol === symbol);
    if (!quote || !["above", "below"].includes(condition) || !Number.isFinite(target) || target <= 0) {
      throw new Error("Choose a stock and enter a valid target price.");
    }
    if ((condition === "above" && target <= quote.price)
      || (condition === "below" && target >= quote.price)) {
      throw new Error(`For an alert ${condition} the current price, choose a target ${condition === "above" ? "above" : "below"} ${money(quote.price)}.`);
    }
    priceAlerts.push({
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      symbol,
      condition,
      target,
      triggered: false,
      created_at: new Date().toISOString()
    });
    savePriceAlerts();
  }

  function checkPriceAlerts() {
    const triggered = [];
    for (const alert of priceAlerts) {
      if (alert.triggered) continue;
      const quote = market?.instruments.find(item => item.symbol === alert.symbol);
      if (!quote) continue;
      const crossed = alert.condition === "above"
        ? quote.price >= alert.target
        : quote.price <= alert.target;
      if (crossed) {
        alert.triggered = true;
        alert.triggered_at = new Date().toISOString();
        triggered.push(`${alert.symbol} reached ${money(quote.price)}`);
      }
    }
    if (triggered.length) {
      savePriceAlerts();
      showNotice(`Price alert: ${triggered.join("; ")}`);
    }
  }

  function priceAlertsTable() {
    if (!priceAlerts.length) return `<div class="empty-state">No price alerts. Add one from a stock page or here in Settings.</div>`;
    return `<div class="workspace-table-wrap"><table class="workspace-table"><thead><tr><th>Stock</th><th>Condition</th><th>Target</th><th>Status</th><th>Action</th></tr></thead><tbody>${priceAlerts.map(alert => `<tr><td>${escapeHtml(alert.symbol)}</td><td>${alert.condition === "above" ? "At or above" : "At or below"}</td><td>${money(alert.target)}</td><td>${alert.triggered ? "Triggered" : "Waiting"}</td><td><button class="workspace-button" data-remove-alert="${escapeHtml(alert.id)}">Remove</button></td></tr>`).join("")}</tbody></table></div>`;
  }

  function startAutoRefresh() {
    if (refreshTimer !== null) window.clearInterval(refreshTimer);
    refreshTimer = window.setInterval(async () => {
      if (document.activeElement && root.contains(document.activeElement)) return;
      try {
        await refreshData();
        checkPriceAlerts();
        render();
      } catch (error) {
        showNotice(error.message, true);
      }
    }, refreshInterval);
  }

  function exportAccountCsv() {
    const rows = [["Record", "Time", "Order ID", "Symbol", "Side", "Type", "Quantity", "Filled quantity", "Price", "Status"]];
    for (const order of paper?.orders || []) {
      rows.push([
        "Order", order.created_at, order.order_id, order.symbol, order.side,
        order.type, order.quantity, order.filled_quantity, order.average_fill_price || order.price, order.status
      ]);
    }
    for (const fill of paper?.executions || []) {
      rows.push([
        "Execution", fill.time, fill.order_id, fill.symbol, fill.side, "FILL",
        fill.quantity, fill.quantity, fill.price, "filled"
      ]);
    }
    const csv = rows.map(row => row.map(value =>
      `"${String(value ?? "").replace(/"/g, '""')}"`
    ).join(",")).join("\r\n");
    const blob = new Blob([csv], {type:"text/csv;charset=utf-8"});
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "chronosmatch-paper-account.csv";
    link.hidden = true;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    showNotice("Paper orders and executions exported.");
  }

  function addRecent(symbol) {
    recentlyViewed = [symbol, ...recentlyViewed.filter(item => item !== symbol)].slice(0, 8);
    try {
      localStorage.setItem("chronosmatch-recent", JSON.stringify(recentlyViewed));
    } catch {
      // Recent views remain available for this page session.
    }
  }

  async function selectStock(symbol) {
    selectedSymbol = symbol;
    addRecent(symbol);
    activeView = "stocks";
    try {
      detail = await getJson(`/api/market/instrument/${encodeURIComponent(symbol)}?range=${selectedRange}`);
      render();
    } catch (error) {
      showNotice(error.message, true);
      render();
    }
  }

  function wireStockRows() {
    root.querySelectorAll("tr[data-symbol]").forEach(row => row.addEventListener("click", event => {
      if (event.target.closest("[data-watch]")) return;
      selectStock(row.dataset.symbol);
    }));
    root.querySelectorAll("[data-watch]").forEach(button => button.addEventListener("click", event => {
      event.stopPropagation();
      const symbol = button.dataset.watch;
      watchlist = watchlist.includes(symbol)
        ? watchlist.filter(item => item !== symbol)
        : [...watchlist, symbol];
      saveWatchlist();
      render();
    }));
  }

  function wirePageControls() {
    wireStockRows();
    root.querySelectorAll("[data-range]").forEach(button => button.addEventListener("click", async () => {
      selectedRange = button.dataset.range;
      await selectStock(selectedSymbol);
    }));
    root.querySelectorAll("[data-trade]").forEach(button => button.addEventListener("click", () => {
      activeView = "trading";
      render();
      const side = root.querySelector('[name="side"]');
      if (side) {
        side.value = button.dataset.trade;
        side.dispatchEvent(new Event("change", {bubbles:true}));
      }
    }));
    root.querySelectorAll("[data-cancel]").forEach(button => button.addEventListener("click", async () => {
      if (!window.confirm(`Cancel paper order ${button.dataset.cancel}?`)) return;
      try {
        await postJson("/api/paper/cancel", {order_id: Number(button.dataset.cancel)});
        await refreshData();
        showNotice("Paper order cancelled.");
        render();
      } catch (error) {
        showNotice(error.message, true);
      }
    }));

    const orderFilterControl = document.getElementById("order-filter");
    if (orderFilterControl) orderFilterControl.addEventListener("change", () => {
      orderFilter = orderFilterControl.value;
      render();
    });
    const cancelAllButton = document.getElementById("cancel-all-orders");
    if (cancelAllButton) cancelAllButton.addEventListener("click", async () => {
      const openOrders = (paper?.orders || []).filter(order =>
        ["open", "partially_filled"].includes(order.status) && order.remaining_quantity
      );
      if (!openOrders.length || !window.confirm(`Cancel all ${openOrders.length} open paper orders?`)) return;
      let cancelled = 0;
      const failures = [];
      for (const order of openOrders) {
        try {
          await postJson("/api/paper/cancel", {order_id: order.order_id});
          cancelled += 1;
        } catch (error) {
          failures.push(`#${order.order_id}: ${error.message}`);
        }
      }
      await refreshData();
      showNotice(failures.length
        ? `Cancelled ${cancelled}; could not cancel ${failures.length}: ${failures.join("; ")}`
        : `Cancelled ${cancelled} open orders.`);
      noticeIsError = failures.length > 0;
      render();
    });
    root.querySelectorAll("[data-remove-alert]").forEach(button => button.addEventListener("click", () => {
      priceAlerts = priceAlerts.filter(alert => alert.id !== button.dataset.removeAlert);
      savePriceAlerts();
      showNotice("Price alert removed.");
      render();
    }));

    const search = document.getElementById("stock-search");
    if (search) search.addEventListener("input", () => {
      stockSearch = search.value.trim().toLocaleLowerCase();
      const list = document.getElementById("stock-list");
      if (list) list.innerHTML = stockTable(filteredStocks());
      wireStockRows();
    });
    const filters = [
      ["exchange-filter", value => { exchangeFilter = value; }],
      ["sector-filter", value => { sectorFilter = value; }],
      ["stock-sort", value => { sortBy = value; }]
    ];
    filters.forEach(([id, update]) => {
      const element = document.getElementById(id);
      if (element) element.addEventListener("change", () => {
        update(element.value);
        const list = document.getElementById("stock-list");
        if (list) list.innerHTML = stockTable(filteredStocks());
        wireStockRows();
      });
    });

    const form = document.getElementById("paper-order-form");
    if (form) {
      const typeSelect = form.elements.type;
      let marketPreview = null;
      let previewKey = "";
      let previewRequest = 0;
      const currentPreviewKey = () =>
        `${form.elements.symbol.value}:${form.elements.side.value}`;
      const refreshMarketPreview = async () => {
        const key = currentPreviewKey();
        const requestId = ++previewRequest;
        marketPreview = null;
        previewKey = "";
        updateEstimate();
        try {
          const result = await getJson(
            `/api/paper/preview?symbol=${encodeURIComponent(form.elements.symbol.value)}&side=${encodeURIComponent(form.elements.side.value)}`
          );
          if (requestId !== previewRequest || !form.isConnected) return;
          marketPreview = result;
          previewKey = key;
          updateEstimate();
        } catch (error) {
          if (requestId !== previewRequest || !form.isConnected) return;
          showNotice(error.message, true);
          updateEstimate();
        }
      };
      const updateEstimate = () => {
        if (!form.isConnected) return;
        const qty = Number(form.elements.quantity.value) || 0;
        const usePreview = typeSelect.value === "MARKET"
          && previewKey === currentPreviewKey() && marketPreview;
        const quotePrice = Number(market?.instruments.find(
          item => item.symbol === form.elements.symbol.value
        )?.price || 0);
        const price = typeSelect.value === "MARKET"
          ? usePreview ? marketPreview.estimated_price : quotePrice
          : Number(form.elements.price.value) || 0;
        document.getElementById("order-estimate").textContent = money(qty * price);
        document.getElementById("order-estimate-label").textContent =
          typeSelect.value === "MARKET" ? "Estimated maximum value" : "Estimated value";
        document.getElementById("limit-price-field").classList.toggle("app-hidden", typeSelect.value === "MARKET");
        form.elements.price.required = typeSelect.value === "LIMIT";
        const isPending = typeSelect.value === "MARKET" && !usePreview;
        const available = usePreview
          ? marketPreview.max_quantity
          : form.elements.side.value === "BUY"
            ? Math.floor((paper?.buying_power || 0) / (price || Infinity))
            : Number(paper?.holdings.find(item => item.symbol === form.elements.symbol.value)?.available_quantity || 0);
        document.getElementById("order-available").textContent =
          isPending ? "Checking current order book…"
            : form.elements.side.value === "BUY"
              ? `Max affordable: ${number(available)} shares`
              : `Available to sell: ${number(available)} shares`;
        document.getElementById("order-risk").textContent =
          !isPending && qty > available ? "Quantity exceeds currently available funds or shares." : "";
        document.getElementById("submit-paper-order").disabled =
          isPending || qty < 1 || qty > available;
      };
      form.addEventListener("input", updateEstimate);
      form.addEventListener("change", updateEstimate);
      form.elements.symbol.addEventListener("change", () => {
        selectedSymbol = form.elements.symbol.value;
        const selectedInstrument = document.getElementById("selected-instrument");
        if (selectedInstrument) selectedInstrument.textContent = selectedSymbol;
        const quote = market?.instruments.find(item => item.symbol === form.elements.symbol.value);
        if (quote && typeSelect.value === "LIMIT") form.elements.price.value = quote.price.toFixed(2);
        if (typeSelect.value === "MARKET") refreshMarketPreview();
        else updateEstimate();
      });
      form.elements.side.addEventListener("change", () => {
        if (typeSelect.value === "MARKET") refreshMarketPreview();
        else updateEstimate();
      });
      typeSelect.addEventListener("change", () => {
        if (typeSelect.value === "MARKET") refreshMarketPreview();
        else {
          previewRequest += 1;
          updateEstimate();
        }
      });
      const maxButton = document.getElementById("use-max-quantity");
      maxButton.addEventListener("click", async () => {
        if (typeSelect.value === "MARKET" && previewKey !== currentPreviewKey()) {
          await refreshMarketPreview();
        }
        const maxQuantity = typeSelect.value === "MARKET"
          ? marketPreview?.max_quantity || 0
          : form.elements.side.value === "BUY"
            ? Math.floor((paper?.buying_power || 0) / (Number(form.elements.price.value) || Infinity))
            : Number(paper?.holdings.find(item => item.symbol === form.elements.symbol.value)?.available_quantity || 0);
        form.elements.quantity.value = String(maxQuantity);
        updateEstimate();
      });
      updateEstimate();
      if (typeSelect.value === "MARKET") refreshMarketPreview();
      form.addEventListener("submit", async event => {
        event.preventDefault();
        const data = new FormData(form);
        const order = {
          symbol: data.get("symbol"),
          side: data.get("side"),
          type: data.get("type"),
          quantity: Number(data.get("quantity")),
          price: data.get("type") === "LIMIT" ? Number(data.get("price")) : null
        };
        if (!window.confirm(`Submit ${order.side} ${order.quantity} ${order.symbol} ${order.type} paper order?`)) return;
        const submitButton = document.getElementById("submit-paper-order");
        submitButton.disabled = true;
        try {
          const result = await postJson("/api/paper/order", order);
          await refreshData();
          showNotice(`Paper order ${result.order_id}: ${result.status}, ${result.filled_quantity} filled.`);
          activeView = "orders";
          render();
        } catch (error) {
          showNotice(error.message, true);
          submitButton.disabled = false;
        }
      });
    }

    for (const alertFormId of ["stock-alert-form", "settings-alert-form"]) {
      const alertForm = document.getElementById(alertFormId);
      if (!alertForm) continue;
      const updateAlertTarget = () => {
        const symbol = alertFormId === "stock-alert-form" ? selectedSymbol : alertForm.elements.symbol.value;
        const price = Number(market?.instruments.find(item => item.symbol === symbol)?.price || 0);
        const factor = alertForm.elements.condition.value === "above" ? 1.02 : 0.98;
        alertForm.elements.target.value = (price * factor).toFixed(2);
      };
      updateAlertTarget();
      alertForm.elements.condition.addEventListener("change", updateAlertTarget);
      if (alertFormId === "settings-alert-form") {
        alertForm.elements.symbol.addEventListener("change", updateAlertTarget);
      }
      alertForm.addEventListener("submit", event => {
        event.preventDefault();
        const data = new FormData(alertForm);
        try {
          addPriceAlert(
            alertFormId === "stock-alert-form" ? selectedSymbol : String(data.get("symbol")),
            String(data.get("condition")),
            Number(data.get("target"))
          );
          showNotice("Price alert saved.");
          render();
        } catch (error) {
          showNotice(error.message, true);
        }
      });
    }

    const resetButton = document.getElementById("reset-paper-account");
    if (resetButton) resetButton.addEventListener("click", async () => {
      if (!window.confirm("Reset paper cash, holdings, orders, and execution history? This cannot be undone.")) return;
      try {
        paper = await postJson("/api/paper/reset");
        showNotice("Paper account reset.");
        render();
      } catch (error) {
        showNotice(error.message, true);
      }
    });
    const watchButton = document.getElementById("clear-watchlist");
    if (watchButton) watchButton.addEventListener("click", () => {
      watchlist = [...defaultWatchlist];
      saveWatchlist();
      render();
    });
    const clearRecentButton = document.getElementById("clear-recent");
    if (clearRecentButton) clearRecentButton.addEventListener("click", () => {
      recentlyViewed = [];
      localStorage.removeItem("chronosmatch-recent");
      showNotice("Recently viewed symbols cleared.");
      render();
    });
    const intervalControl = document.getElementById("refresh-interval");
    if (intervalControl) intervalControl.addEventListener("change", () => {
      refreshInterval = Number(intervalControl.value);
      localStorage.setItem(refreshStorageKey, String(refreshInterval));
      startAutoRefresh();
      showNotice(`Refresh interval set to ${refreshInterval / 1000} seconds.`);
      render();
    });
    const exportButton = document.getElementById("export-account");
    if (exportButton) exportButton.addEventListener("click", exportAccountCsv);
    root.querySelectorAll("[data-sell-symbol]").forEach(button => button.addEventListener("click", event => {
      event.stopPropagation();
      selectedSymbol = button.dataset.sellSymbol;
      activeView = "trading";
      render();
      const form = document.getElementById("paper-order-form");
      if (form) {
        form.elements.side.value = "SELL";
        form.elements.quantity.value = String(paper?.holdings.find(item => item.symbol === selectedSymbol)?.available_quantity || 0);
        form.dispatchEvent(new Event("change", {bubbles:true}));
      }
    }));
  }

  async function refreshData() {
    const [nextMarket, nextPaper] = await Promise.all([
      getJson("/api/market/state"),
      getJson("/api/paper/state")
    ]);
    market = nextMarket;
    paper = nextPaper;
    if (!market.instruments.some(stock => stock.symbol === selectedSymbol)) {
      selectedSymbol = market.instruments[0]?.symbol || "";
    }
    if (activeView === "stocks" && selectedSymbol) {
      detail = await getJson(`/api/market/instrument/${encodeURIComponent(selectedSymbol)}?range=${selectedRange}`);
    }
  }

  nav.addEventListener("click", async event => {
    const button = event.target.closest("button[data-view]");
    if (!button || !views.has(button.dataset.view)) return;
    activeView = button.dataset.view;
    notice = "";
    try {
      await refreshData();
      checkPriceAlerts();
      render();
    } catch (error) {
      showNotice(error.message, true);
      render();
    }
  });

  async function initialize() {
    try {
      await refreshData();
      checkPriceAlerts();
      render();
      startAutoRefresh();
    } catch (error) {
      root.innerHTML = `<div class="workspace-panel"><h1>Dashboard unavailable</h1><p>${escapeHtml(error.message)}</p></div>`;
    }
  }

  initialize();
})();
