(globalThis.TURBOPACK||(globalThis.TURBOPACK=[])).push(["object"==typeof document?document.currentScript:void 0,82418,e=>{"use strict";var s=e.i(47167),a=e.i(43476),t=e.i(71645);let i=s.default.env.NEXT_PUBLIC_TRADER_API_URL??"",r=e=>`${e>=0?"+":"−"}$${Math.abs(e).toFixed(2)}`;function n(e){return e>0?"pos":e<0?"neg":"flat"}e.s(["BotPerformanceBoard",0,function(){let[e,s]=(0,t.useState)(null),[d,l]=(0,t.useState)(null);(0,t.useEffect)(()=>{let e=!0,a=async()=>{try{let a=await fetch(`${i}/api/bots`,{cache:"no-store"});if(!a.ok)throw Error(`HTTP ${a.status}`);let t=await a.json();e&&(s(t),l(null))}catch(s){e&&l(s instanceof Error?s.message:String(s))}};a();let t=setInterval(a,5e3);return()=>{e=!1,clearInterval(t)}},[]);let c=e?.bots.reduce((e,s)=>e+s.equityImpactUsd,0)??0;return(0,a.jsxs)("main",{className:"board",children:[(0,a.jsxs)("header",{className:"head",children:[(0,a.jsxs)("div",{children:[(0,a.jsx)("h1",{children:"Bot Performance"}),(0,a.jsx)("p",{className:"sub",children:"Three strategies, one demo account, separated by magic number."})]}),(0,a.jsx)("div",{className:"acct",children:e?.accountEquityUsd!=null&&(0,a.jsxs)(a.Fragment,{children:[(0,a.jsxs)("span",{className:"acct-eq",children:["$",e.accountEquityUsd.toFixed(2)]}),(0,a.jsxs)("span",{className:"acct-lbl",children:["equity · ",e.openPositions," open"]})]})})]}),d&&(0,a.jsxs)("div",{className:"err",children:["Cannot reach the trader service",i?` at ${i}`:""," — ",d]}),!e&&!d&&(0,a.jsx)("div",{className:"err",children:"Loading…"}),(0,a.jsx)("div",{className:"grid",children:e?.bots.map(e=>(0,a.jsxs)("section",{className:`card ${n(e.equityImpactUsd)}`,children:[(0,a.jsxs)("div",{className:"card-top",children:[(0,a.jsx)("h2",{children:e.label}),(0,a.jsxs)("span",{className:"magic",children:["#",e.magicNumber]})]}),(0,a.jsx)("div",{className:`headline ${n(e.equityImpactUsd)}`,children:r(e.equityImpactUsd)}),(0,a.jsxs)("div",{className:"headline-sub",children:[r(e.realisedUsd)," realised",0!==e.unrealisedUsd&&(0,a.jsxs)(a.Fragment,{children:[" · ",r(e.unrealisedUsd)," open"]})]}),(0,a.jsxs)("dl",{className:"stats",children:[(0,a.jsxs)("div",{children:[(0,a.jsx)("dt",{children:"Trades"}),(0,a.jsx)("dd",{children:e.trades})]}),(0,a.jsxs)("div",{children:[(0,a.jsx)("dt",{children:"Win rate"}),(0,a.jsx)("dd",{children:e.trades?`${e.winRate}%`:"—"})]}),(0,a.jsxs)("div",{children:[(0,a.jsx)("dt",{children:"Avg / trade"}),(0,a.jsx)("dd",{className:n(e.averageUsd),children:e.trades?r(e.averageUsd):"—"})]}),(0,a.jsxs)("div",{children:[(0,a.jsx)("dt",{children:"Open"}),(0,a.jsxs)("dd",{children:[e.openPositions,e.openVolume>0&&(0,a.jsxs)("span",{className:"dim",children:[" · ",e.openVolume," lots"]})]})]}),(0,a.jsxs)("div",{children:[(0,a.jsx)("dt",{children:"Best"}),(0,a.jsx)("dd",{className:"pos",children:e.trades?r(e.bestUsd):"—"})]}),(0,a.jsxs)("div",{children:[(0,a.jsx)("dt",{children:"Worst"}),(0,a.jsx)("dd",{className:"neg",children:e.trades?r(e.worstUsd):"—"})]})]}),(0,a.jsx)("footer",{className:"card-foot",children:e.symbols.length>0?e.symbols.join(" · "):"no trades yet"})]},e.botId))}),e&&e.bots.length>0&&(0,a.jsxs)("div",{className:"total",children:[(0,a.jsx)("span",{children:"Combined"}),(0,a.jsx)("strong",{className:n(c),children:r(c)})]}),(0,a.jsx)("style",{children:`
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
                     padding-top: 12px; margin-top: auto; }
        .total { display: flex; justify-content: space-between; align-items: baseline;
                 margin-top: 24px; padding-top: 18px;
                 border-top: 1px solid rgba(128,128,128,0.22); font-size: 14px; }
        .total strong { font-size: 24px; font-variant-numeric: tabular-nums; }
        @media (max-width: 480px) { .stats { grid-template-columns: 1fr; } }
      `})]})}])}]);