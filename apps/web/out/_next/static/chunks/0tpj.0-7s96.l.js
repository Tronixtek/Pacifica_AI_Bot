(globalThis.TURBOPACK||(globalThis.TURBOPACK=[])).push(["object"==typeof document?document.currentScript:void 0,82418,e=>{"use strict";var s=e.i(43476),a=e.i(71645);let t="http://127.0.0.1:8011",i=e=>`${e>=0?"+":"−"}$${Math.abs(e).toFixed(2)}`;function r(e){return e>0?"pos":e<0?"neg":"flat"}e.s(["BotPerformanceBoard",0,function(){let[e,n]=(0,a.useState)(null),[d,l]=(0,a.useState)(null);(0,a.useEffect)(()=>{let e=!0,s=async()=>{try{let s=await fetch(`${t}/api/bots`,{cache:"no-store"});if(!s.ok)throw Error(`HTTP ${s.status}`);let a=await s.json();e&&(n(a),l(null))}catch(s){e&&l(s instanceof Error?s.message:String(s))}};s();let a=setInterval(s,5e3);return()=>{e=!1,clearInterval(a)}},[]);let c=e?.bots.reduce((e,s)=>e+s.equityImpactUsd,0)??0;return(0,s.jsxs)("main",{className:"board",children:[(0,s.jsxs)("header",{className:"head",children:[(0,s.jsxs)("div",{children:[(0,s.jsx)("h1",{children:"Bot Performance"}),(0,s.jsx)("p",{className:"sub",children:"Three strategies, one demo account, separated by magic number."})]}),(0,s.jsx)("div",{className:"acct",children:e?.accountEquityUsd!=null&&(0,s.jsxs)(s.Fragment,{children:[(0,s.jsxs)("span",{className:"acct-eq",children:["$",e.accountEquityUsd.toFixed(2)]}),(0,s.jsxs)("span",{className:"acct-lbl",children:["equity · ",e.openPositions," open"]})]})})]}),d&&(0,s.jsxs)("div",{className:"err",children:["Cannot reach the trader service",` at ${t}`," — ",d]}),!e&&!d&&(0,s.jsx)("div",{className:"err",children:"Loading…"}),(0,s.jsx)("div",{className:"grid",children:e?.bots.map(e=>(0,s.jsxs)("section",{className:`card ${r(e.equityImpactUsd)}`,children:[(0,s.jsxs)("div",{className:"card-top",children:[(0,s.jsx)("h2",{children:e.label}),(0,s.jsxs)("span",{className:"magic",children:["#",e.magicNumber]})]}),(0,s.jsx)("div",{className:`headline ${r(e.equityImpactUsd)}`,children:i(e.equityImpactUsd)}),(0,s.jsxs)("div",{className:"headline-sub",children:[i(e.realisedUsd)," realised",0!==e.unrealisedUsd&&(0,s.jsxs)(s.Fragment,{children:[" · ",i(e.unrealisedUsd)," open"]})]}),(0,s.jsxs)("dl",{className:"stats",children:[(0,s.jsxs)("div",{children:[(0,s.jsx)("dt",{children:"Trades"}),(0,s.jsx)("dd",{children:e.trades})]}),(0,s.jsxs)("div",{children:[(0,s.jsx)("dt",{children:"Win rate"}),(0,s.jsx)("dd",{children:e.trades?`${e.winRate}%`:"—"})]}),(0,s.jsxs)("div",{children:[(0,s.jsx)("dt",{children:"Avg / trade"}),(0,s.jsx)("dd",{className:r(e.averageUsd),children:e.trades?i(e.averageUsd):"—"})]}),(0,s.jsxs)("div",{children:[(0,s.jsx)("dt",{children:"Open"}),(0,s.jsxs)("dd",{children:[e.openPositions,e.openVolume>0&&(0,s.jsxs)("span",{className:"dim",children:[" · ",e.openVolume," lots"]})]})]}),(0,s.jsxs)("div",{children:[(0,s.jsx)("dt",{children:"Best"}),(0,s.jsx)("dd",{className:"pos",children:e.trades?i(e.bestUsd):"—"})]}),(0,s.jsxs)("div",{children:[(0,s.jsx)("dt",{children:"Worst"}),(0,s.jsx)("dd",{className:"neg",children:e.trades?i(e.worstUsd):"—"})]})]}),(0,s.jsx)("footer",{className:"card-foot",children:e.symbols.length>0?e.symbols.join(" · "):"no trades yet"})]},e.botId))}),e&&e.bots.length>0&&(0,s.jsxs)("div",{className:"total",children:[(0,s.jsx)("span",{children:"Combined"}),(0,s.jsx)("strong",{className:r(c),children:i(c)})]}),(0,s.jsx)("style",{children:`
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