(globalThis.TURBOPACK||(globalThis.TURBOPACK=[])).push(["object"==typeof document?document.currentScript:void 0,82418,e=>{"use strict";var a=e.i(47167),s=e.i(43476),t=e.i(71645);let r=a.default.env.NEXT_PUBLIC_TRADER_API_URL??"",i=e=>`${e>=0?"+":"−"}$${Math.abs(e).toFixed(2)}`;function n(e){return e>0?"pos":e<0?"neg":"flat"}e.s(["BotPerformanceBoard",0,function(){let[e,a]=(0,t.useState)(null),[d,o]=(0,t.useState)(null),[l,c]=(0,t.useState)(null),[p,x]=(0,t.useState)(null),m=async e=>{c(e.botId);try{let s=e.paused?"resume":"pause",t=await fetch(`${r}/api/bots/${e.botId}/${s}`,{method:"POST"}),i=await t.json();x(i.message??null),a(a=>a?{...a,bots:a.bots.map(a=>a.botId===e.botId?{...a,paused:i.paused}:a)}:a)}catch(e){x(e instanceof Error?e.message:String(e))}finally{c(null)}};(0,t.useEffect)(()=>{let e=!0,s=async()=>{try{let s=await fetch(`${r}/api/bots`,{cache:"no-store"});if(!s.ok)throw Error(`HTTP ${s.status}`);let t=await s.json();e&&(a(t),o(null))}catch(a){e&&o(a instanceof Error?a.message:String(a))}};s();let t=setInterval(s,5e3);return()=>{e=!1,clearInterval(t)}},[]);let u=e?.bots.reduce((e,a)=>e+a.equityImpactUsd,0)??0;return(0,s.jsxs)("main",{className:"board",children:[(0,s.jsxs)("header",{className:"head",children:[(0,s.jsxs)("div",{children:[(0,s.jsx)("h1",{children:"Bot Performance"}),(0,s.jsx)("p",{className:"sub",children:"Three strategies, one demo account, separated by magic number."})]}),(0,s.jsx)("div",{className:"acct",children:e?.accountEquityUsd!=null&&(0,s.jsxs)(s.Fragment,{children:[(0,s.jsxs)("span",{className:"acct-eq",children:["$",e.accountEquityUsd.toFixed(2)]}),(0,s.jsxs)("span",{className:"acct-lbl",children:["equity · ",e.openPositions," open"]})]})})]}),d&&(0,s.jsxs)("div",{className:"err",children:["Cannot reach the trader service",r?` at ${r}`:""," — ",d]}),!e&&!d&&(0,s.jsx)("div",{className:"err",children:"Loading…"}),p&&(0,s.jsx)("div",{className:"notice",onClick:()=>x(null),children:p}),(0,s.jsx)("div",{className:"grid",children:e?.bots.map(e=>(0,s.jsxs)("section",{className:`card ${n(e.equityImpactUsd)}`,children:[(0,s.jsxs)("div",{className:"card-top",children:[(0,s.jsx)("h2",{children:e.label}),(0,s.jsxs)("span",{className:"magic",children:["#",e.magicNumber]})]}),e.archived?(0,s.jsxs)("div",{className:"archived-flag",children:[(0,s.jsx)("strong",{children:"ARCHIVED"}),"— retired, not running.",e.archivedReason?` ${e.archivedReason}`:""," ","Figures below are history."]}):e.paused?(0,s.jsx)("div",{className:"paused-flag",children:"Paused — no new trades. Open positions still managed."}):null,(0,s.jsx)("div",{className:`headline ${n(e.equityImpactUsd)}`,children:i(e.equityImpactUsd)}),(0,s.jsxs)("div",{className:"headline-sub",children:[i(e.realisedUsd)," realised",0!==e.unrealisedUsd&&(0,s.jsxs)(s.Fragment,{children:[" · ",i(e.unrealisedUsd)," open"]})]}),(0,s.jsxs)("dl",{className:"stats",children:[(0,s.jsxs)("div",{children:[(0,s.jsx)("dt",{children:"Trades"}),(0,s.jsx)("dd",{children:e.trades})]}),(0,s.jsxs)("div",{children:[(0,s.jsx)("dt",{children:"Win rate"}),(0,s.jsx)("dd",{children:e.trades?`${e.winRate}%`:"—"})]}),(0,s.jsxs)("div",{children:[(0,s.jsx)("dt",{children:"Avg / trade"}),(0,s.jsx)("dd",{className:n(e.averageUsd),children:e.trades?i(e.averageUsd):"—"})]}),(0,s.jsxs)("div",{children:[(0,s.jsx)("dt",{children:"Open"}),(0,s.jsxs)("dd",{children:[e.openPositions,e.openVolume>0&&(0,s.jsxs)("span",{className:"dim",children:[" · ",e.openVolume," lots"]})]})]}),(0,s.jsxs)("div",{children:[(0,s.jsx)("dt",{children:"Best"}),(0,s.jsx)("dd",{className:"pos",children:e.trades?i(e.bestUsd):"—"})]}),(0,s.jsxs)("div",{children:[(0,s.jsx)("dt",{children:"Worst"}),(0,s.jsx)("dd",{className:"neg",children:e.trades?i(e.worstUsd):"—"})]})]}),(0,s.jsxs)("footer",{className:"card-foot",children:[(0,s.jsx)("span",{children:e.symbols.length>0?e.symbols.join(" · "):"no trades yet"}),e.canPause&&!e.archived&&(0,s.jsx)("button",{className:e.paused?"btn resume":"btn pause",onClick:()=>m(e),disabled:l===e.botId,children:l===e.botId?"…":e.paused?"Resume":"Stop"})]})]},e.botId))}),e&&e.bots.length>0&&(0,s.jsxs)("div",{className:"total",children:[(0,s.jsx)("span",{children:"Combined"}),(0,s.jsx)("strong",{className:n(u),children:i(u)})]}),(0,s.jsx)("style",{children:`
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