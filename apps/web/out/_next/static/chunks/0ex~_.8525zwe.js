(globalThis.TURBOPACK||(globalThis.TURBOPACK=[])).push(["object"==typeof document?document.currentScript:void 0,82418,e=>{"use strict";var a=e.i(47167),s=e.i(43476),r=e.i(71645);let t=a.default.env.NEXT_PUBLIC_TRADER_API_URL??"",n=e=>`${e>=0?"+":"−"}$${Math.abs(e).toFixed(2)}`;function i(e){if(!e)return null;let a=Math.max(0,Math.round((Date.now()-new Date(e).getTime())/1e3));return a<60?`${a}s ago`:a<3600?`${Math.round(a/60)}m ago`:`${Math.round(a/3600)}h ago`}function o(e){return e>0?"pos":e<0?"neg":"flat"}e.s(["BotPerformanceBoard",0,function(){let[e,a]=(0,r.useState)(null),[d,l]=(0,r.useState)(null),[c,p]=(0,r.useState)(null),[x,g]=(0,r.useState)(null),[m,h]=(0,r.useState)(null),b=async e=>{p(e.botId);try{let s=e.paused?"resume":"pause",r=await fetch(`${t}/api/bots/${e.botId}/${s}`,{method:"POST"}),n=await r.json();g(n.message??null),a(a=>a?{...a,bots:a.bots.map(a=>a.botId===e.botId?{...a,paused:n.paused}:a)}:a)}catch(e){g(e instanceof Error?e.message:String(e))}finally{p(null)}};(0,r.useEffect)(()=>{let e=!0,s=async()=>{try{let s=await fetch(`${t}/api/bots`,{cache:"no-store"});if(!s.ok)throw Error(`HTTP ${s.status}`);let r=await s.json();e&&(a(r),l(null));try{let a=await fetch(`${t}/api/bots/edge/activity`,{cache:"no-store"});a.ok&&e&&h(await a.json())}catch{}}catch(a){e&&l(a instanceof Error?a.message:String(a))}};s();let r=setInterval(s,5e3);return()=>{e=!1,clearInterval(r)}},[]);let u=e?.bots.reduce((e,a)=>e+a.equityImpactUsd,0)??0;return(0,s.jsxs)("main",{className:"board",children:[(0,s.jsxs)("header",{className:"head",children:[(0,s.jsxs)("div",{children:[(0,s.jsx)("h1",{children:"Bot Performance"}),(0,s.jsx)("p",{className:"sub",children:"Three strategies, one demo account, separated by magic number."})]}),(0,s.jsx)("div",{className:"acct",children:e?.accountEquityUsd!=null&&(0,s.jsxs)(s.Fragment,{children:[(0,s.jsxs)("span",{className:"acct-eq",children:["$",e.accountEquityUsd.toFixed(2)]}),(0,s.jsxs)("span",{className:"acct-lbl",children:["equity · ",e.openPositions," open"]})]})})]}),d&&(0,s.jsxs)("div",{className:"err",children:["Cannot reach the trader service",t?` at ${t}`:""," — ",d]}),!e&&!d&&(0,s.jsx)("div",{className:"err",children:"Loading…"}),x&&(0,s.jsx)("div",{className:"notice",onClick:()=>g(null),children:x}),(0,s.jsx)("div",{className:"grid",children:e?.bots.map(e=>(0,s.jsxs)("section",{className:`card ${o(e.equityImpactUsd)}`,children:[(0,s.jsxs)("div",{className:"card-top",children:[(0,s.jsx)("h2",{children:e.label}),(0,s.jsxs)("span",{className:"magic",children:["#",e.magicNumber]})]}),e.archived?(0,s.jsxs)("div",{className:"archived-flag",children:[(0,s.jsx)("strong",{children:"ARCHIVED"}),"— retired, not running.",e.archivedReason?` ${e.archivedReason}`:""," ","Figures below are history."]}):e.paused?(0,s.jsx)("div",{className:"paused-flag",children:"Paused — no new trades. Open positions still managed."}):null,(0,s.jsx)("div",{className:`headline ${o(e.equityImpactUsd)}`,children:n(e.equityImpactUsd)}),(0,s.jsxs)("div",{className:"headline-sub",children:[n(e.realisedUsd)," realised",0!==e.unrealisedUsd&&(0,s.jsxs)(s.Fragment,{children:[" · ",n(e.unrealisedUsd)," open"]})]}),(0,s.jsxs)("dl",{className:"stats",children:[(0,s.jsxs)("div",{children:[(0,s.jsx)("dt",{children:"Trades"}),(0,s.jsx)("dd",{children:e.trades})]}),(0,s.jsxs)("div",{children:[(0,s.jsx)("dt",{children:"Win rate"}),(0,s.jsx)("dd",{children:e.trades?`${e.winRate}%`:"—"})]}),(0,s.jsxs)("div",{children:[(0,s.jsx)("dt",{children:"Avg / trade"}),(0,s.jsx)("dd",{className:o(e.averageUsd),children:e.trades?n(e.averageUsd):"—"})]}),(0,s.jsxs)("div",{children:[(0,s.jsx)("dt",{children:"Open"}),(0,s.jsxs)("dd",{children:[e.openPositions,e.openVolume>0&&(0,s.jsxs)("span",{className:"dim",children:[" · ",e.openVolume," lots"]})]})]}),(0,s.jsxs)("div",{children:[(0,s.jsx)("dt",{children:"Best"}),(0,s.jsx)("dd",{className:"pos",children:e.trades?n(e.bestUsd):"—"})]}),(0,s.jsxs)("div",{children:[(0,s.jsx)("dt",{children:"Worst"}),(0,s.jsx)("dd",{className:"neg",children:e.trades?n(e.worstUsd):"—"})]})]}),(0,s.jsxs)("footer",{className:"card-foot",children:[(0,s.jsx)("span",{children:e.symbols.length>0?e.symbols.join(" · "):"no trades yet"}),e.canPause&&!e.archived&&(0,s.jsx)("button",{className:e.paused?"btn resume":"btn pause",onClick:()=>b(e),disabled:c===e.botId,children:c===e.botId?"…":e.paused?"Resume":"Stop"})]})]},e.botId))}),m&&(0,s.jsxs)("section",{className:"live",children:[(0,s.jsxs)("div",{className:"live-head",children:[(0,s.jsx)("h2",{children:"What the bot is seeing"}),(0,s.jsxs)("span",{className:"live-meta",children:[m.signalsSeen," signal",1===m.signalsSeen?"":"s"," / ",m.declined," declined",m.paused?" / PAUSED":""," / updated ",i(m.generatedAt)??"-"]})]}),0===m.markets.length&&(0,s.jsx)("p",{className:"reason",children:m.running?"Starting up - the first observation lands within a poll.":"The edge bot is not running."}),(0,s.jsx)("div",{className:"obs",children:m.markets.map(e=>(0,s.jsxs)("div",{className:`ob ${"signal"===e.status?"hot":""}`,children:[(0,s.jsxs)("div",{className:"ob-top",children:[(0,s.jsx)("strong",{children:e.symbol}),(0,s.jsxs)("span",{className:"tf",children:[e.timeframe,e.higherTimeframe?` under ${e.higherTimeframe}`:""]})]}),(0,s.jsxs)("div",{className:"trends",children:[(0,s.jsxs)("span",{className:`pill ${e.trend??"none"}`,children:[e.timeframe," ",e.trend??"no trend"]}),e.higherTimeframe&&(0,s.jsxs)("span",{className:`pill ${e.higherTrend??"none"}`,children:[e.higherTimeframe," ",e.higherTrend??"no trend"]}),e.trend&&e.higherTrend&&e.trend!==e.higherTrend&&(0,s.jsx)("span",{className:"pill blocked",children:"vetoed"})]}),(0,s.jsx)("p",{className:"reason",children:e.reason}),(0,s.jsxs)("div",{className:"ob-facts",children:[null!=e.bid&&null!=e.ask?(0,s.jsxs)("span",{className:"quote",children:[e.bid," / ",e.ask]}):null!=e.price&&(0,s.jsxs)("span",{children:["price ",e.price]}),null!=e.atr&&(0,s.jsxs)("span",{children:["ATR ",e.atr.toFixed(2)]}),null!=e.spreadFractionOfAtr&&(0,s.jsxs)("span",{className:e.spreadFractionOfAtr>.25?"warn":"",title:"Spread as a fraction of ATR. This ratio decided every result measured.",children:["spread ",(100*e.spreadFractionOfAtr).toFixed(1),"% of ATR"]}),e.barClosedAt&&(0,s.jsxs)("span",{children:["bar ",new Date(e.barClosedAt).toISOString().slice(11,16)," UTC"]}),e.observedAt&&(0,s.jsxs)("span",{className:Date.now()-new Date(e.observedAt).getTime()>12e4?"warn":"fresh",title:"How long ago the bot last looked at this market. If this stops moving, the loop has stopped.",children:["seen ",i(e.observedAt)]})]})]},e.symbol))}),m.topReasons.length>0&&(0,s.jsxs)("div",{className:"reasons",children:[(0,s.jsx)("span",{className:"reasons-label",children:"Why it is waiting"}),m.topReasons.map(([e,a])=>(0,s.jsxs)("div",{className:"reason-row",children:[(0,s.jsx)("span",{className:"count",children:a}),(0,s.jsx)("span",{children:e})]},e))]}),m.refusals.length>0&&(0,s.jsxs)("div",{className:"reasons",children:[(0,s.jsx)("span",{className:"reasons-label",children:"Qualified but declined"}),m.refusals.slice(0,6).map((e,a)=>(0,s.jsxs)("div",{className:"reason-row",children:[(0,s.jsx)("span",{className:"count",children:new Date(e.at).toISOString().slice(11,16)}),(0,s.jsxs)("span",{children:[(0,s.jsxs)("strong",{children:[e.symbol," ",e.direction]}),e.pattern?` (${e.pattern})`:""," - ",e.reason]})]},a))]}),m.events.length>0&&(0,s.jsxs)("details",{className:"log",children:[(0,s.jsxs)("summary",{children:["Activity log (",m.events.length,")"]}),(0,s.jsx)("pre",{children:m.events.join("\n")})]})]}),e&&e.bots.length>0&&(0,s.jsxs)("div",{className:"total",children:[(0,s.jsx)("span",{children:"Combined"}),(0,s.jsx)("strong",{className:o(u),children:n(u)})]}),(0,s.jsx)("style",{children:`

        .live { margin-top: 32px; border: 1px solid rgba(148,163,184,0.22);
          border-radius: 12px; padding: 18px 20px 20px; }
        .live-head { display: flex; justify-content: space-between; align-items: baseline;
          gap: 16px; flex-wrap: wrap; margin-bottom: 14px; }
        .live h2 { font-size: 15px; font-weight: 600; margin: 0; letter-spacing: -0.01em; }
        .live-meta { font-size: 12px; opacity: 0.6; font-variant-numeric: tabular-nums; }
        .obs { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
        .ob { border: 1px solid rgba(148,163,184,0.18); border-radius: 9px; padding: 12px 14px; }
        .ob.hot { border-color: rgba(52,211,153,0.55); background: rgba(52,211,153,0.07); }
        .ob-top { display: flex; justify-content: space-between; align-items: baseline; gap: 10px; }
        .ob-top strong { font-size: 14px; }
        .tf { font-size: 11px; opacity: 0.55; }
        .trends { display: flex; gap: 6px; flex-wrap: wrap; margin: 9px 0; }
        .pill { font-size: 10px; letter-spacing: 0.04em; text-transform: uppercase;
          padding: 3px 7px; border-radius: 5px; border: 1px solid transparent; }
        .pill.up { color: #6ee7b7; border-color: rgba(110,231,183,0.35); background: rgba(110,231,183,0.10); }
        .pill.down { color: #fca5a5; border-color: rgba(252,165,165,0.35); background: rgba(252,165,165,0.10); }
        .pill.none { color: #94a3b8; border-color: rgba(148,163,184,0.3); }
        .pill.blocked { color: #fcd34d; border-color: rgba(252,211,77,0.4); background: rgba(252,211,77,0.10); }
        .reason { font-size: 12px; line-height: 1.5; margin: 0 0 9px; opacity: 0.78; }
        .ob-facts { display: flex; gap: 12px; flex-wrap: wrap; font-size: 11px;
          opacity: 0.5; font-variant-numeric: tabular-nums; }
        .ob-facts .warn { color: #fcd34d; opacity: 1; }
        .ob-facts .fresh { color: #6ee7b7; opacity: 0.85; }
        .ob-facts .quote { font-weight: 600; opacity: 0.85; }
        .reasons { margin-top: 16px; }
        .reasons-label { display: block; font-size: 11px; text-transform: uppercase;
          letter-spacing: 0.07em; opacity: 0.45; margin-bottom: 7px; }
        .reason-row { display: flex; gap: 10px; font-size: 12px; padding: 3px 0;
          line-height: 1.5; opacity: 0.8; }
        .reason-row .count { min-width: 46px; text-align: right; opacity: 0.55;
          font-variant-numeric: tabular-nums; }
        .log { margin-top: 16px; font-size: 12px; }
        .log summary { cursor: pointer; opacity: 0.55; }
        .log pre { margin: 9px 0 0; padding: 11px; border-radius: 7px; overflow-x: auto;
          background: rgba(148,163,184,0.08); font-size: 11px; line-height: 1.65; }
        .board { max-width: 1100px; margin: 0 auto; padding: 40px 24px 64px; }
        .head { display: flex; justify-content: space-between; align-items: flex-start;
                gap: 24px; flex-wrap: wrap; margin-bottom: 28px; }
        h1 { font-size: 26px; font-weight: 600; letter-spacing: -0.02em; margin: 0; }
        .sub { margin: 6px 0 0; font-size: 14px; opacity: 0.6; }
        .acct { text-align: right; }
        .acct-eq { display: block; font-size: 26px; font-weight: 600;
                   font-variant-numeric: tabular-nums; }
        .acct-lbl { font-size: 12px; opacity: 0.55; }
        .err { border: 1px solid rgba(220,60,60,0.35); background: rgba(220,60,60,0.07);
               padding: 14px 16px; border-radius: 10px; font-size: 14px; margin-bottom: 20px; }
        .grid { display: grid; gap: 16px;
                grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); }
        .card { border: 1px solid rgba(128,128,128,0.22); border-radius: 14px;
                padding: 20px; display: flex; flex-direction: column; gap: 4px; }
        .card.pos { border-color: rgba(38,166,110,0.45); }
        .card.neg { border-color: rgba(220,70,70,0.40); }
        .card:has(.paused-flag) { opacity: 0.72; }
        .card:has(.archived-flag) { opacity: 0.55; filter: grayscale(0.8); }
        .card-top { display: flex; justify-content: space-between; align-items: baseline;
                    gap: 12px; margin-bottom: 10px; }
        h2 { font-size: 15px; font-weight: 600; margin: 0; }
        .magic { font-size: 11px; opacity: 0.45; font-variant-numeric: tabular-nums; }
        .headline { font-size: 32px; font-weight: 650; letter-spacing: -0.02em;
                    font-variant-numeric: tabular-nums; }
        .headline-sub { font-size: 12px; opacity: 0.6; margin-bottom: 16px;
                        font-variant-numeric: tabular-nums; }
        .pos { color: #26a66e; }
        .neg { color: #dc4646; }
        .flat { opacity: 0.75; }
        .stats { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 16px;
                 margin: 0 0 16px; }
        .stats div { display: flex; flex-direction: column; gap: 2px; }
        dt { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em;
             opacity: 0.5; }
        dd { margin: 0; font-size: 15px; font-weight: 550;
             font-variant-numeric: tabular-nums; }
        .dim { font-weight: 400; opacity: 0.5; font-size: 12px; }
        .card-foot { font-size: 11px; opacity: 0.45; border-top: 1px solid rgba(128,128,128,0.15);
                     padding-top: 12px; margin-top: auto; display: flex;
                     align-items: center; justify-content: space-between; gap: 12px; }
        .btn { font: inherit; font-size: 12px; font-weight: 550; padding: 7px 16px;
               border-radius: 8px; cursor: pointer; border: 1px solid transparent;
               background: transparent; opacity: 1; }
        /* Comfortably past the 44px touch target once padding is counted -
           this is used one-handed on a phone. */
        .btn { min-height: 34px; min-width: 78px; }
        .btn.pause { border-color: rgba(220,70,70,0.5); color: #dc4646; }
        .btn.pause:hover { background: rgba(220,70,70,0.1); }
        .btn.resume { border-color: rgba(38,166,110,0.55); color: #26a66e; }
        .btn.resume:hover { background: rgba(38,166,110,0.12); }
        .btn:disabled { opacity: 0.45; cursor: default; }
        .archived-flag { font-size: 11px; padding: 8px 10px; margin-bottom: 12px;
          border-radius: 6px; line-height: 1.5;
          border: 1px solid rgba(148,163,184,0.45);
          background: rgba(100,116,139,0.16); color: #94a3b8; }
        .archived-flag strong { letter-spacing: 0.08em; color: #cbd5e1; }
        .paused-flag { font-size: 11px; padding: 6px 10px; margin-bottom: 12px;
                       border-radius: 7px; background: rgba(220,160,40,0.13);
                       border: 1px solid rgba(220,160,40,0.35); }
        .notice { border: 1px solid rgba(128,128,128,0.3); border-radius: 10px;
                  padding: 12px 16px; font-size: 13px; margin-bottom: 18px;
                  cursor: pointer; }
        .total { display: flex; justify-content: space-between; align-items: baseline;
                 margin-top: 24px; padding-top: 18px;
                 border-top: 1px solid rgba(128,128,128,0.22); font-size: 14px; }
        .total strong { font-size: 24px; font-variant-numeric: tabular-nums; }
        @media (max-width: 480px) { .stats { grid-template-columns: 1fr; } }
      `})]})}])}]);