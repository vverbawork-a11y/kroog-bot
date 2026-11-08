"use strict";
const $ = id => document.getElementById(id);
const tg = window.Telegram?.WebApp;
const money = n => n.toLocaleString("ru-RU") + " ₽";
const panels = ["shop","inventory","about","receipt"];
let catalog, color, requestId, busy = false, choosing = true;
function display(panel) {
  for (const name of panels) $(name).hidden = name !== panel;
  for (const [id,name] of [["shopNav","shop"],["aboutNav","about"],["stockNav","inventory"]]) {
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
  const pct = catalog.discounts.find(([min])=>qty >= min)?.[1] || 0;
  const subtotal = qty*unit;
  // Python round() uses ties-to-even for the discount in rubles.
  const raw = subtotal*pct/100;
  const discount = raw % 1 === 0.5 && Math.floor(raw)%2===0 ? Math.floor(raw):Math.round(raw);
  $("total").textContent = money(subtotal-discount);
  $("discount").textContent = pct ? "Скидка за тираж "+pct+"% · −"+money(discount):"";
}
function choose(c, fromClick=false) {
  if (fromClick) {choosing=false;requestId=null;window.scrollTo(0,0);}
  $("selectedName").textContent=c.name+" · доступно к заказу "+c.stock+" шт.";
  $("colors").hidden=!choosing;$("colorHeading").hidden=!choosing;
  color = c;
  for (const b of $("colors").children) b.setAttribute("aria-pressed",String(Number(b.dataset.id)===c.id));
  $("qty").max = c.stock;
  $("qty").value = Math.min(c.stock,Math.max(1,Number($("qty").value)));
  $("form").hidden = choosing;
  prices();
}
async function load() {
  catalog = await api("catalog");
  $("stockNav").hidden = !catalog.admin;
  if (catalog.channel) { $("channel").href = catalog.channel; $("channel").hidden = false; }
  if (!$("contact").value) $("contact").value = catalog.contact;
  $("sleevePrice").textContent = "+"+money(catalog.sleeve)+" за экземпляр";
  $("labelPrice").textContent = "Две стороны · +"+money(catalog.label)+" за экземпляр";
  $("rushPrice").textContent = "+"+catalog.rush+"% · срок согласует менеджер";
  const list = catalog.colors.filter(c=>c.stock>0);
  $("colors").replaceChildren();
  for (const c of list) {
    const tile = button("",()=>choose(c,true));
    tile.className="tile"; tile.dataset.id=c.id;
    const pic=document.createElement(c.photo ? "img":"span");
    if (c.photo) {pic.src=c.photo;pic.alt=c.name;pic.loading="lazy";}
    else {pic.className="stock-empty";pic.textContent=c.emoji || "○";pic.setAttribute("aria-label","Фотография пока недоступна");}
    const caption=document.createElement("span"),title=document.createElement("strong"),info=document.createElement("small");
    caption.className="caption";title.textContent=c.name;info.textContent="Доступно к заказу: "+c.stock+" шт. · "+money(c.price)+" / пластинка";
    caption.append(title,info);tile.append(pic,caption);$("colors").append(tile);
  }
  if (list.length) choose(list.find(c=>c.id===color?.id)||list[0]);
  else {color=null;choosing=true;$("colors").hidden=false;$("colorHeading").hidden=false;$("form").hidden=true;$("colors").textContent="Сейчас заготовок в наличии нет.";}
  if (catalog.admin) renderStock();
}
function renderStock() {
  $("stocks").replaceChildren();
  for (const c of catalog.colors) {
    const row=document.createElement("div"),label=document.createElement("label"),input=document.createElement("input");
    row.className="stockrow";label.textContent=c.name;input.type="number";input.min=0;input.max=1000000;
    input.value=c.stock;input.id="stock-"+c.id;label.htmlFor=input.id;
    const save=button("Сохранить",async()=>{
      save.disabled=true;
      try {
        await api("stock",{color:c.id,stock:input.valueAsNumber,previous:c.stock});
        await load();$("status").textContent=c.name+" · сохранено";
      } catch(e) {await load().catch(()=>{});$("status").textContent=e.message;}
      finally {save.disabled=false;}
    });
    row.append(label,input,save);$("stocks").append(row);
  }
}
const ARTWORK = [
  ["sleeve_front","sleeve"],["sleeve_back","sleeve"],
  ["label_a","label"],["label_b","label"]
];
const ARTWORK_NAMES = {
  sleeve_front:"Конверт · передняя сторона",
  sleeve_back:"Конверт · задняя сторона",
  label_a:"Яблоко · сторона A",
  label_b:"Яблоко · сторона B"
};
function syncArtwork() {
  $("sleeveFiles").hidden = !$("sleeve").checked;
  $("labelFiles").hidden = !$("label").checked;
  for (const [key] of ARTWORK) {
    const file = $(key).files[0];
    $(key+"_name").textContent = file ? file.name : "Файл не выбран";
  }
}
for (const [key] of ARTWORK) {
  $(key).addEventListener("change",()=>{
    const file = $(key).files[0];
    if (file && file.size > 20*1024*1024) {
      $(key).value = "";
      $("status").textContent = "Файл больше 20 МБ. Прикрепи файл меньшего размера.";
    }
    requestId=null;
    syncArtwork();
  });
}
$("form").oninput=()=>{requestId=null;prices();syncArtwork();};
syncArtwork();
$("minus").onclick=()=>{$("qty").stepDown();requestId=null;prices();};
$("plus").onclick=()=>{$("qty").stepUp();requestId=null;prices();};
$("form").onsubmit=async e=>{
  e.preventDefault();if (!color || busy) return;busy=true;$("submit").disabled=true;
  requestId ||= crypto.randomUUID();
  try {
    const payload = {color:color.id,quantity:$("qty").valueAsNumber,
      sleeve:$("sleeve").checked,label:$("label").checked,rush:$("rush").checked,
      contact:$("contact").value,request_id:requestId};
    const formData = new FormData();
    formData.append("payload", JSON.stringify(payload));
    for (const [key,option] of ARTWORK) {
      if ($(option).checked && $(key).files[0])
        formData.append(key, $(key).files[0], $(key).files[0].name);
    }
    const result=await api("orders",formData);
    display("receipt");$("receiptTitle").textContent="Заказ №"+result.id;
    $("receiptText").textContent="Заявка принята. Менеджер свяжется с тобой по указанному контакту.";
    $("cancelOrder").hidden=true;requestId=null;$("form").reset();syncArtwork();
  } catch(error) {await load().catch(()=>{});$("status").textContent=error.message;}
  finally {busy=false;$("submit").disabled=false;}
};
async function detail(id) {
  const o=await api("orders/"+id);
  display("receipt");$("receiptTitle").textContent="Заказ №"+o.id;
  const opts=[o.sleeve&&"цветной конверт",o.label&&"цветное яблоко",o.rush&&"срочно"].filter(Boolean).join(", ");
  $("receiptText").textContent=[o.cancelled?"Отменён":"Принят",o.color+" × "+o.quantity,opts||"Без доплат",
    "Итого "+money(o.total),o.name,o.contact,"Telegram ID "+o.user_id].join("\n");
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
  $("cancelOrder").hidden=o.cancelled;
  $("cancelOrder").onclick=async()=>{
    if(!confirm("Отменить заказ и вернуть "+o.quantity+" шт. на склад?"))return;
    try {await api("orders/"+id+"/cancel",{});await detail(id);}catch(e){$("status").textContent=e.message;}
  };
}
async function openShop(){choosing=true;display("shop");try{await load();}catch(e){$("status").textContent=e.message;}}
$("changeColor").onclick=openShop;$("shopNav").onclick=openShop;$("back").onclick=openShop;
$("aboutNav").onclick=()=>display("about");
$("stockNav").onclick=async()=>{display("inventory");try{await load();}catch(e){$("status").textContent=e.message;}};
async function start(){
  tg?.ready();tg?.expand();display("shop");
  if (!tg?.initData) {$("status").textContent="Открой приложение из меню @kroog_vinyl_bot.";return;}
  try {await load();const id=new URLSearchParams(location.search).get("order");if(id && catalog.admin && /^\d+$/.test(id))await detail(id);}
  catch(e){$("status").textContent=e.message;}
}
start();
