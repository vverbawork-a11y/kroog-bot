"use strict";
const $ = id => document.getElementById(id);
const tg = window.Telegram?.WebApp;
const money = n => n.toLocaleString("ru-RU") + " ₽";
const panels = ["shop","inventory","about","receipt","inquiry","works","faq"];
let catalog, color, requestId, busy = false, choosing = true;
let interestColor = null, interestRequestId = null;
let activePromo = null, promoEpoch = 0;
function display(panel) {
  $("markPaid").hidden=true;
  document.body.dataset.panel=panel;
  for (const name of panels) $(name).hidden = name !== panel;
  for (const [id,name] of [["shopNav","shop"],["aboutNav","about"],["worksNav","works"],["faqNav","faq"],["stockNav","inventory"]]) {
    $(id).setAttribute("aria-current",String(name === panel));
  }
  $("status").textContent = "";
  window.scrollTo(0,0);
}
async function api(path,data) {
  const isForm = data instanceof FormData;
  const headers = {"X-Telegram-Init-Data":tg?.initData || ""};
  if (data && !isForm) headers["Content-Type"] = "application/json";
  const response = await fetch("/api/"+path,{method:data ? "POST":"GET",
    headers, body:data ? (isForm ? data : JSON.stringify(data)):undefined,cache:"no-store"});
  if (!response.ok) throw new Error((await response.text()).slice(0,240));
  return response.json();
}
function button(text,action) {
  const b = document.createElement("button");
  b.type = "button"; b.textContent = text; b.onclick = action;
  return b;
}
function prices() {
  if (!color) return;
  const qty = Number($("qty").value);
  let unit = color.price + ($("sleeve").checked ? catalog.sleeve:0) + ($("label").checked ? catalog.label:0);
  if ($("rush").checked) unit = Math.round(unit*(100+catalog.rush)/100);
  const volumePct = catalog.discounts.find(([min])=>qty >= min)?.[1] || 0;
  const promoWins = activePromo && activePromo.percent > volumePct;
  const pct = Math.max(volumePct, activePromo?.percent || 0);
  const subtotal = qty*unit;
  // Python round() uses ties-to-even for the discount in rubles.
  const raw = subtotal*pct/100;
  const discount = raw % 1 === 0.5 && Math.floor(raw)%2===0 ? Math.floor(raw):Math.round(raw);
  $("total").textContent = money(subtotal-discount);
  $("discount").textContent = pct ? (promoWins ? "Промокод "+activePromo.code : "Скидка за тираж")+" "+pct+"% · −"+money(discount):"";
  if (activePromo) $("promoMessage").textContent = promoWins
    ? "Промокод применён. Скидка "+activePromo.percent+"%."
    : "Скидка за тираж выгоднее или равна. Промокод не расходуется.";
}
function choose(c, fromClick=false) {
  if (fromClick) {choosing=false;requestId=null;}
  $("selectedName").textContent=c.name+" · доступно к заказу "+c.stock+" шт.";
  $("colors").hidden=!choosing;
  $("colorHeading").hidden=!choosing;
  $("stockHint").hidden=!choosing;
  color = c;
  for (const b of $("colors").children) b.setAttribute("aria-pressed",String(Number(b.dataset.id)===c.id));
  $("qty").max = c.stock;
  $("qty").value = Math.min(c.stock,Math.max(1,Number($("qty").value)));
  $("form").hidden = choosing;
  prices();
  if (fromClick) {
    requestAnimationFrame(()=>{
      // Form replaces the catalogue at the same scroll position, not below it.
      const top = window.scrollY + document.querySelector("header").getBoundingClientRect().top;
      window.scrollTo(0,Math.max(0,top));
    });
  }
}
async function load() {
  catalog = await api("catalog");
  $("stockNav").hidden = !catalog.admin;
  $("promoManager").hidden = !catalog.admin;
  const managerLink = $("faqManagerLink");
  managerLink.hidden = !catalog.manager_url;
  if (catalog.manager_url) managerLink.href = catalog.manager_url;
  if (catalog.channel) { $("channel").href = catalog.channel; $("channel").hidden = false; }
  if (!$("contact").value) $("contact").value = catalog.contact;
  $("sleevePrice").textContent = "+"+money(catalog.sleeve)+" за экземпляр";
  $("labelPrice").textContent = "Две стороны · +"+money(catalog.label)+" за экземпляр";
  $("rushPrice").textContent = "+"+catalog.rush+"% · приоритет в очереди";
  const available = catalog.colors.filter(c=>c.stock>0);
  $("colors").replaceChildren();
  function makeTile(c, isAvailable) {
    const tile = button("",()=>isAvailable ? choose(c,true) : showInquiry(c));
    tile.className = isAvailable ? "tile" : (c.incoming ? "tile tile-incoming" : "tile tile-unavailable");
    tile.dataset.id=c.id;
    let pic;
    if (Number.isInteger(c.atlas) && c.atlas>=0 && c.atlas<32) {
      pic=document.createElement("img");
      // Original supplier JPG is unchanged. Display the normalized preview.
      const filename=String(c.atlas).padStart(2,"0")+".jpg";
      pic.src="/assets/previews/"+filename+"?v=13";
      pic.addEventListener("error",()=>{
        if (!pic.dataset.fallback) {
          pic.dataset.fallback="true";
          pic.src="/assets/supplier/"+filename;
        }
      });
      pic.className="vinyl-photo";
      pic.alt=c.name;
      pic.loading="lazy";
      pic.decoding="async";
      pic.width=800;
      pic.height=800;
    } else if (c.photo) {
      pic=document.createElement("img");
      pic.src=c.photo;pic.alt=c.name;pic.loading="lazy";pic.className="vinyl-photo";
    } else {
      pic=document.createElement("span");
      pic.className="stock-empty";
      pic.textContent=c.emoji || "○";
      pic.setAttribute("aria-label","Фото пока недоступно");
    }
    const media=document.createElement("span");
    media.className="tile-media";
    if (pic.tagName === "IMG") media.classList.add("has-vinyl-photo");
    media.append(pic);
    if (!isAvailable) {
      const flag=document.createElement("span");
      flag.className="stock-overlay";
      flag.textContent=c.incoming ? "Скоро в наличии" : "Нет в наличии";
      media.append(flag);
    }
    const caption=document.createElement("span");
    const title=document.createElement("strong");
    const info=document.createElement("small");
    caption.className="caption";title.textContent=c.name;
    info.textContent=isAvailable
      ? "Доступно: "+c.stock+" шт. · от "+money(c.price)
      : "От "+money(c.price)+" · "+(c.incoming ? "Скоро в наличии" : "Нет в наличии");
    caption.append(title,info);tile.append(media,caption);
    return tile;
  }
  // One continuous gallery: in stock, on the way, unavailable.
  for (const c of catalog.colors) $("colors").append(makeTile(c,c.stock>0));
  if (available.length) {
    const existing=available.find(c=>c.id===color?.id)||available[0];
    choose(existing);
  }
  else {
    color=null;choosing=true;
    $("colors").hidden=false;$("colorHeading").hidden=false;$("form").hidden=true;
    $("stockHint").hidden=false;
    $("colors").hidden=false;
    // All unavailable colours remain visible and can be requested.

  }
  if (catalog.admin) renderStock();
}
function showInquiry(c) {
  interestColor=c;
  interestRequestId=null;
  $("inquiryColor").textContent=c.name+" · "+(c.incoming ? "Скоро в наличии" : "Нет в наличии") +" · от "+money(c.price)+" за пластинку";
  if (!$("inquiryContact").value) $("inquiryContact").value=catalog?.contact || "";
  display("inquiry");
}
$("inquiryForm").onsubmit=async e=>{
  e.preventDefault();
  if(!interestColor || busy) return;
  busy=true;$("inquirySubmit").disabled=true;
  interestRequestId ||= crypto.randomUUID();
  try {
    const response=await api("inquiries",{
      color:interestColor.id,contact:$("inquiryContact").value,request_id:interestRequestId
    });
    display("receipt");
    $("receiptTitle").textContent="Запрос №"+response.id;
    $("receiptText").textContent="Запрос по цвету «"+interestColor.name+
      "» отправлен. Мы свяжемся с вами по указанному контакту.";
    $("receiptFiles").replaceChildren();
    $("cancelOrder").hidden=true;$("markPaid").hidden=true;
    interestRequestId=null;
  } catch(error) {$("status").textContent=error.message;}
  finally {busy=false;$("inquirySubmit").disabled=false;}
};
$("inquiryBack").onclick=()=>openShop();


function renderStock() {
  $("stocks").replaceChildren();
  for (const c of catalog.colors) {
    const row=document.createElement("div");
    const label=document.createElement("label");
    const input=document.createElement("input");
    row.className="stockrow";label.textContent=c.name;
    input.type="number";input.min=0;input.max=1000000;
    input.value=c.stock;input.id="stock-"+c.id;label.htmlFor=input.id;
    const arrival=document.createElement("label");
    arrival.className="arrival-toggle";
    const checkbox=document.createElement("input");
    checkbox.type="checkbox";checkbox.checked=Boolean(c.incoming);
    checkbox.disabled=Number(c.stock)>0;
    const arrivalText=document.createElement("span");
    arrivalText.textContent="В пути";
    arrival.append(checkbox,arrivalText);
    input.addEventListener("input",()=>{
      checkbox.disabled=Number(input.value)>0;
      if (checkbox.disabled)checkbox.checked=false;
    });
    const save=button("Сохранить",async()=>{
      save.disabled=true;
      try {
        await api("stock",{color:c.id,stock:input.valueAsNumber,previous:c.stock,
          incoming:checkbox.checked,previous_incoming:Boolean(c.incoming)});
        await load();$("status").textContent=c.name+" · сохранено";
      } catch(e) {await load().catch(()=>{});$("status").textContent=e.message;}
      finally {save.disabled=false;}
    });
    row.append(label,input,arrival,save);$("stocks").append(row);
  }
}
function orderPayload() {
  return {color:color?.id,quantity:$("qty").valueAsNumber,
    sleeve:$("sleeve").checked,label:$("label").checked,rush:$("rush").checked,
    promo_code:activePromo?.code || ""};
}
async function applyPromo() {
  const code=$("promoCode").value.trim().toUpperCase();
  const epoch=++promoEpoch;
  activePromo=null;prices();
  if (!code) {$("promoMessage").textContent="Введите промокод.";return false;}
  $("applyPromo").disabled=true;$("promoMessage").textContent="Проверяем промокод…";
  try {
    const result=await api("quote",{...orderPayload(),promo_code:code});
    if (epoch!==promoEpoch) return false;
    activePromo={code:result.promo_code,percent:result.promo_percent};
    $("promoCode").value=activePromo.code;
    prices();return true;
  } catch(error) {
    if(epoch===promoEpoch) {$("promoMessage").textContent=error.message;prices();}
    return false;
  } finally {$("applyPromo").disabled=false;}
}
function resetPromo() {
  activePromo=null;promoEpoch++;
  $("promoCode").value="";$("promoMessage").textContent="";$("promoEntry").open=false;
}
$("applyPromo").onclick=applyPromo;
$("promoCode").addEventListener("keydown",event=>{
  if(event.key==="Enter") {event.preventDefault();applyPromo();}
});
async function loadPromos() {
  const response=await api("promos");renderPromos(response.promos);
}
function renderPromos(promos) {
  $("promoList").replaceChildren();
  if(!promos.length) {
    const empty=document.createElement("p");empty.className="help";
    empty.textContent="Промокодов пока нет. Размер скидки выбираете вы.";
    $("promoList").append(empty);return;
  }
  for(const promo of promos) {
    const row=document.createElement("div");row.className="promo-row";
    const info=document.createElement("div"), title=document.createElement("strong"), note=document.createElement("small");
    const expired=promo.expires_at && promo.expires_at*1000<=Date.now();
    title.textContent=promo.code+" · "+promo.percent+"%";
    const conditions=[promo.active ? (expired ? "Истёк" : "Включён") : "Отключён",
      "Использований: "+promo.used+(promo.max_uses ? " из "+promo.max_uses : "")];
    if(promo.expires_at) conditions.push("До "+new Date(promo.expires_at*1000).toLocaleString("ru-RU"));
    if(promo.user_id) conditions.push("Получатель: "+promo.user_id);
    if(promo.returning_only) conditions.push("Для повторного заказа");
    note.textContent=conditions.join(" · ");info.append(title,note);
    const actions=document.createElement("div");
    actions.className="promo-row-actions";
    const toggle=button(promo.active ? "Отключить" : "Включить",async()=>{
      toggle.disabled=true;remove.disabled=true;
      try {
        const result=await api("promos/"+promo.id,{active:!promo.active});
        renderPromos(result.promos);
        $("promoAdminStatus").textContent="";
      }
      catch(error){$("promoAdminStatus").textContent=error.message;toggle.disabled=false;remove.disabled=false;}
    });
    const remove=button("×",async()=>{
      if(!confirm('Удалить промокод «'+promo.code+'»? Восстановить его будет нельзя.')) return;
      remove.disabled=true;toggle.disabled=true;
      try {
        const result=await api("promos/"+promo.id,{delete:true});
        renderPromos(result.promos);
        $("promoAdminStatus").textContent="Промокод «"+promo.code+"» удалён.";
      }
      catch(error){$("promoAdminStatus").textContent=error.message;remove.disabled=false;toggle.disabled=false;}
    });
    remove.className="promo-delete";
    remove.setAttribute("aria-label","Удалить промокод "+promo.code);
    remove.title="Удалить промокод";
    actions.append(toggle,remove);
    row.append(info,actions);$("promoList").append(row);
  }
}
$("promoAdminForm").onsubmit=async event=>{
  event.preventDefault();$("createPromo").disabled=true;
  const expiry=$("newPromoExpiry").value;
  try {
    const result=await api("promos",{
      code:$("newPromoCode").value,percent:$("newPromoPercent").valueAsNumber,
      max_uses:$("newPromoLimit").value ? $("newPromoLimit").valueAsNumber : null,
      expires_at:expiry ? Math.floor(new Date(expiry).getTime()/1000) : null,
      user_id:$("newPromoUser").value ? $("newPromoUser").valueAsNumber : null,
      returning_only:$("newPromoReturning").checked
    });
    renderPromos(result.promos);$("promoAdminForm").reset();
    $("promoAdminStatus").textContent="Промокод создан. Его можно передать получателям.";
  }catch(error){$("promoAdminStatus").textContent=error.message;}
  finally{$("createPromo").disabled=false;}
};

// Minimal checkout: audio, tracklist and print files are collected by the manager.
$("rpmFaqLink").onclick=()=>{
  display("faq");
  requestAnimationFrame(()=>$("faqSpeed").scrollIntoView({block:"start",behavior:"auto"}));
};
$("form").oninput=event=>{
  requestId=null;promoEpoch++;
  if(event.target.id==="promoCode") activePromo=null;
  if(!activePromo) $("promoMessage").textContent="";
  prices();
};
$("minus").onclick=()=>{$("qty").stepDown();requestId=null;prices();};
$("plus").onclick=()=>{$("qty").stepUp();requestId=null;prices();};
$("form").onsubmit=async e=>{
  e.preventDefault();if (!color || busy) return;busy=true;$("submit").disabled=true;
  requestId ||= crypto.randomUUID();
  try {
    if ($("promoCode").value.trim() && activePromo?.code !== $("promoCode").value.trim().toUpperCase()) {
      if(!await applyPromo()) {$("promoEntry").open=true;return;}
    }
    const payload = {...orderPayload(),contact:$("contact").value,request_id:requestId,
      rpm:Number(document.querySelector('input[name="rpm"]:checked')?.value||33)};
    const result=await api("orders",payload);
    display("receipt");$("receiptTitle").textContent="Заказ №"+result.id;
    $("receiptText").textContent="Заказ оформлен! Менеджер свяжется с вами, чтобы получить музыку и макеты, обсудить детали и оплату. Неоплаченный заказ автоматически отменяется через 24 часа после оформления.";
    $("cancelOrder").hidden=true;$("markPaid").hidden=true;$("receiptFiles").replaceChildren();requestId=null;$("form").reset();resetPromo();
  } catch(error) {
    if(/промокод/i.test(error.message)) {
      activePromo=null;promoEpoch++;$("promoEntry").open=true;
      $("promoMessage").textContent=error.message;
    }
    await load().catch(()=>{});$("status").textContent=error.message;
  }
  finally {busy=false;$("submit").disabled=false;}
};
async function detail(id) {
  const o=await api("orders/"+id);
  display("receipt");$("receiptTitle").textContent="Заказ №"+o.id;
  const opts=[o.sleeve&&"цветной конверт",o.label&&"цветное яблоко",o.rush&&"срочно"].filter(Boolean).join(", ");
  $("receiptText").textContent=[o.cancelled?(o.cancel_reason==="expired"?"Отменён: оплата не подтверждена за 24 часа":"Отменён"):o.paid?"Оплачен":"Ожидает оплаты",o.color+" × "+o.quantity,opts||"Без доплат",
    "Итого "+money(o.total),
    ...(!o.cancelled && !o.paid && o.expires_at ? ["Оплатить до: "+new Date(o.expires_at*1000).toLocaleString("ru-RU")] : []),
    ...(o.promo_applied ? ["Промокод: "+o.promo_code+" · "+o.promo_percent+"%"] : []),
    "Скорость: "+(o.rpm||33)+" RPM",
    (o.tracklist_a?.length ? "A: "+o.tracklist_a.join("; ") : "A: треклист позже"),
    (o.tracklist_b?.length ? "B: "+o.tracklist_b.join("; ") : "B: треклист позже"),
    ...(o.audio_link ? ["Аудио: "+o.audio_link] : []),
    o.name,o.contact,"Telegram ID "+o.user_id].join("\n");
  $("receiptFiles").replaceChildren();
  for (const [key,entry] of Object.entries(o.files || {})) {
    if (!ARTWORK_NAMES[key] || !entry?.name) continue;
    const b = button("Скачать · "+ARTWORK_NAMES[key]+" · "+entry.name,async()=>{
      b.disabled=true;
      try {
        const response=await fetch("/api/orders/"+id+"/artwork/"+key,{
          headers:{"X-Telegram-Init-Data":tg?.initData || ""},cache:"no-store"
        });
        if (!response.ok) throw new Error((await response.text()).slice(0,240));
        const url=URL.createObjectURL(await response.blob());
        const a=document.createElement("a");
        a.href=url;a.download=entry.name;
        document.body.append(a);a.click();a.remove();
        setTimeout(()=>URL.revokeObjectURL(url),60000);
      } catch(e) {$("status").textContent=e.message;}
      finally {b.disabled=false;}
    });
    $("receiptFiles").append(b);
  }
  $("markPaid").hidden=o.cancelled||o.paid;
  $("markPaid").onclick=async()=>{
    if(!confirm("Вы получили оплату за заказ №"+o.id+"? Автоматическая отмена будет отключена."))return;
    $("markPaid").disabled=true;
    try {await api("orders/"+id+"/paid",{});await detail(id);}catch(e){$("status").textContent=e.message;}
    finally {$("markPaid").disabled=false;}
  };
  $("cancelOrder").hidden=o.cancelled;
  $("cancelOrder").onclick=async()=>{
    if(!confirm("Отменить заказ и вернуть "+o.quantity+" шт. на склад?"))return;
    try {await api("orders/"+id+"/cancel",{});await detail(id);}catch(e){$("status").textContent=e.message;}
  };
}
async function openShop(){choosing=true;display("shop");try{await load();}catch(e){$("status").textContent=e.message;}}
$("changeColor").onclick=openShop;$("shopNav").onclick=openShop;$("back").onclick=openShop;
$("aboutNav").onclick=()=>display("about");
$("worksNav").onclick=()=>display("works");
$("faqNav").onclick=()=>display("faq");
$("faqManagerLink").onclick=(event)=>{
  const address=$("faqManagerLink").getAttribute("href");
  if (address?.startsWith("https://t.me/") && tg?.openTelegramLink) {
    event.preventDefault();
    tg.openTelegramLink(address);
  }
};
$("stockNav").onclick=async()=>{display("inventory");try{await load();await loadPromos();}catch(e){$("status").textContent=e.message;}};
async function start(){
  tg?.ready();tg?.expand();display("shop");startBackground();
  if (!tg?.initData) {$("status").textContent="Откройте приложение из меню @kroog_vinyl_bot.";return;}
  try {await load();const id=new URLSearchParams(location.search).get("order");if(id && catalog.admin && /^\d+$/.test(id))await detail(id);}
  catch(e){$("status").textContent=e.message;}
}
start();
