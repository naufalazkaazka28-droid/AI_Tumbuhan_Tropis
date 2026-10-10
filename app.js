(() => {
  const log = document.getElementById("log");
  const form = document.getElementById("f");
  const input = document.getElementById("q");
  const sendBtn = document.getElementById("send");
  const status = document.getElementById("status");
  const chips = [...document.querySelectorAll("#chips button")];
  sendBtn.disabled = true;
  chips.forEach(button => { button.disabled = true; });
  let kb;
  let lastPlant = null;
  let asked = new Set();
  let stamps = [];
  try {
    const saved = JSON.parse(sessionStorage.getItem("tumbuhan-tropis-session") || "{}");
    lastPlant = saved.lastPlant || null;
    asked = new Set(saved.asked || []);
    stamps = saved.stamps || [];
  } catch (error) {
    console.warn("Konteks sesi lokal tidak dapat dipulihkan.", error);
  }

  function add(role, text) {
    const message = document.createElement("div");
    message.className = "msg " + role;
    message.textContent = text;
    log.appendChild(message);
    log.scrollTop = log.scrollHeight;
  }

  function normalize(text) {
    return (text || "")
      .slice(0, 500)
      .normalize("NFKD")
      .replace(/\p{Diacritic}/gu, "")
      .toLowerCase()
      .replace(/https?:\/\/\S+/g, " ")
      .replace(/[^a-z0-9\s]/g, " ")
      .replace(/(.)\1{2,}/g, "$1$1")
      .replace(/\s+/g, " ")
      .trim();
  }

  function editDistance(a, b, limit) {
    if (Math.abs(a.length - b.length) > limit) return limit + 1;
    let previous = Array.from({ length: b.length + 1 }, (_, i) => i);
    for (let i = 1; i <= a.length; i++) {
      const current = [i];
      let rowMin = i;
      for (let j = 1; j <= b.length; j++) {
        current[j] = Math.min(
          current[j - 1] + 1,
          previous[j] + 1,
          previous[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1)
        );
        rowMin = Math.min(rowMin, current[j]);
      }
      if (rowMin > limit) return limit + 1;
      previous = current;
    }
    return previous[b.length];
  }

  function prepareKnowledge(data) {
    if (!data || !data.plants || !data.intents || !Array.isArray(data.rules)) {
      throw new Error("Format basis pengetahuan tidak lengkap.");
    }

    const nameToKey = new Map();
    for (const [key, plant] of Object.entries(data.plants)) {
      for (const name of [key, ...(plant.alias || []), plant.ilmiah]) {
        const phrase = normalize(name);
        if (phrase) nameToKey.set(phrase, key);
      }
    }

    const intentWords = new Set();
    const intents = Object.entries(data.intents).map(([name, spec]) => {
      for (const keyword of spec.keywords) {
        keyword.split(" ").forEach(word => intentWords.add(word));
      }
      return {
        name,
        spec,
        patterns: spec.keywords.map(word => new RegExp("(?<![a-z])" + escapeRegex(word)))
      };
    });

    const vocab = new Set([
      ...(data.stopwords || []),
      ...(data.extra_vocab || []),
      ...(data.domain?.terms || []),
      ...intentWords,
      ...[...nameToKey.keys()].flatMap(phrase => phrase.split(" ")),
      ...Object.values(data.slang || {}).flatMap(value => value.split(" "))
    ]);
    const vocabList = [...vocab].filter(word => word.length >= 3);
    const rules = [...data.rules]
      .sort((a, b) => (b.priority || 0) - (a.priority || 0))
      .map(rule => ({ ...rule, patterns: rule.patterns.map(pattern => new RegExp(pattern)) }));

    return {
      ...data,
      nameToKey,
      phrases: [...nameToKey.keys()].sort((a, b) => b.length - a.length),
      intents,
      intentWords,
      vocabList,
      rules,
      humanPatterns: (data.human_request?.patterns || []).map(pattern => new RegExp(pattern)),
      sensitivePattern: new RegExp(data.sensitive_pattern),
      comparePattern: new RegExp("\\b(" + data.compare_keywords.map(escapeRegex).join("|") + ")\\b")
    };
  }

  function escapeRegex(value) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function processInput(text) {
    const raw = (typeof text === "string" ? text : "").slice(0, kb.settings.max_input_chars);
    const words = [];
    for (const word of normalize(raw).split(" ").filter(Boolean)) {
      const corrected = kb.slang[word] || word;
      for (const part of corrected.split(" ")) {
        let best = part;
        if (!kb.vocabList.includes(part) && part.length >= 5 && !/^\d+$/.test(part)) {
          const maxDistance = Math.floor(part.length * (1 - kb.settings.fuzzy_cutoff));
          let distance = maxDistance + 1;
          for (const candidate of kb.vocabList) {
            const next = editDistance(part, candidate, maxDistance);
            if (next < distance) {
              best = candidate;
              distance = next;
              if (distance === 0) break;
            }
          }
        }
        words.push(best);
      }
    }
    const stopwords = new Set(kb.stopwords);
    return {
      raw,
      words,
      tokens: words.filter(word => !stopwords.has(word) && word.length > 1),
      norm: words.join(" "),
      empty: words.length === 0
    };
  }

  function findPlants(norm) {
    let work = ` ${norm} `;
    const found = [];
    for (const phrase of kb.phrases) {
      const match = work.match(new RegExp("(?<![a-z0-9])" + escapeRegex(phrase) + "(?![a-z0-9])"));
      if (!match || match.index === undefined) continue;
      found.push({ index: match.index, key: kb.nameToKey.get(phrase) });
      work = work.slice(0, match.index) + " ".repeat(match[0].length) + work.slice(match.index + match[0].length);
    }
    return [...new Set(found.sort((a, b) => a.index - b.index).map(item => item.key))];
  }

  function detectIntents(norm) {
    const scores = kb.intents
      .map(({ name, patterns }) => ({ name, score: patterns.filter(pattern => pattern.test(norm)).length }))
      .filter(item => item.score > 0);
    if (!scores.length) return [];
    const highest = Math.max(...scores.map(item => item.score));
    return scores.filter(item => item.score === highest).slice(0, 2).map(item => item.name);
  }

  function plantList() {
    return Object.keys(kb.plants).map(name => name.replace(/\b\w/g, letter => letter.toUpperCase())).join(", ");
  }

  function ruleReply(rule, plants) {
    let text = rule.response.replaceAll("{daftar_tumbuhan}", plantList());
    let plant = null;
    if (rule.type === "symptom" && plants.length) {
      plant = plants[0];
      lastPlant = plant;
      text = `[${plant.replace(/\b\w/g, letter => letter.toUpperCase())}] ${text}`;
    }
    return { text, kind: rule.type === "smalltalk" ? "smalltalk" : "rule", intent: rule.id, plant };
  }

  function answer(match, proc) {
    const key = match.plants[0];
    const plant = kb.plants[key];
    const title = key.replace(/\b\w/g, letter => letter.toUpperCase());
    lastPlant = key;
    let text;

    if (match.intents.length) {
      text = match.intents.map(intent => {
        const spec = kb.intents.find(item => item.name === intent).spec;
        asked.add(`${key}:${intent}`);
        return kb.messages.answer
          .replace("{nama}", title)
          .replace("{ilmiah}", plant.ilmiah)
          .replace("{label}", spec.label)
          .replace("{value}", plant[spec.field]);
      }).join("\n\n");
    } else {
      text = kb.messages.profile
        .replace("{nama}", title)
        .replace("{ilmiah}", plant.ilmiah)
        .replace("{famili}", plant.famili)
        .replace("{ciri}", plant.ciri)
        .replace("{habitat}", plant.habitat)
        .replace("{manfaat}", plant.manfaat);
      ["ciri", "habitat", "manfaat"].forEach(intent => asked.add(`${key}:${intent}`));
    }

    const selected = new Set(match.intents);
    if (plant.herbal && (!match.intents.length || selected.has("manfaat") || selected.has("keamanan"))) {
      text += "\n\n" + kb.messages.herbal_note;
    }
    if (kb.sensitivePattern.test(proc.norm) &&
        (!match.intents.length || selected.has("manfaat") || selected.has("keamanan"))) {
      text += "\n" + kb.messages.sensitive_note;
    }

    const options = ["manfaat", "perawatan", "habitat", "keamanan", "ciri"]
      .filter(intent => !asked.has(`${key}:${intent}`))
      .slice(0, 2);
    if (options.length) {
      text += "\n\n" + kb.messages.suggest.replace(
        "{opsi}",
        options.map(intent => kb.intents.find(item => item.name === intent).spec.hint).join(", ")
      );
    }
    return { text, kind: "answer", intent: match.intents.join(",") || "profile", plant: key };
  }

  function compare(match) {
    const fields = match.intents.length ? match.intents : ["ciri", "habitat", "manfaat"];
    const blocks = match.plants.map(key => {
      const plant = kb.plants[key];
      const lines = fields.map(intent => `${kb.intents.find(item => item.name === intent).spec.label}: ${plant[kb.intents.find(item => item.name === intent).spec.field]}`).join("\n");
      return kb.messages.compare_item
        .replace("{nama}", key.replace(/\b\w/g, letter => letter.toUpperCase()))
        .replace("{ilmiah}", plant.ilmiah)
        .replace("{lines}", lines);
    });
    lastPlant = match.plants[0];
    return { text: blocks.join("\n\n"), kind: "compare", intent: fields.join(","), plant: lastPlant };
  }

  function classify(proc) {
    const { norm, words, tokens } = proc;
    if (kb.humanPatterns.some(pattern => pattern.test(norm))) {
      return {
        kind: "local_only",
        text: "Chatbot ini berjalan mandiri di perangkat dan tidak terhubung ke petugas atau layanan pesan."
      };
    }
    const plants = findPlants(norm);
    for (const term of kb.domain.out_of_scope_plants) {
      if (new RegExp("(?<![a-z])" + escapeRegex(term) + "(?![a-z])").test(norm)) {
        return { kind: "out_of_scope", term };
      }
    }
    if (!plants.length) {
      const phrase = kb.domain.offtopic_phrases.find(item => norm.includes(item));
      if (phrase) return { kind: "offtopic" };
    }

    const smalltalk = kb.rules.find(rule =>
      rule.type === "smalltalk" &&
      words.length <= (rule.max_words || kb.settings.greeting_max_words) &&
      rule.patterns.every(pattern => pattern.test(norm))
    );
    if (smalltalk) return { ...ruleReply(smalltalk, plants), kind: "smalltalk" };

    const intents = detectIntents(norm);
    if (plants.length >= 2 && kb.comparePattern.test(norm)) {
      return compare({ plants: plants.slice(0, 3), intents: intents.slice(0, 1) });
    }

    const rule = kb.rules.find(candidate =>
      candidate.type !== "smalltalk" &&
      !(candidate.no_plant && plants.length) &&
      candidate.patterns.every(pattern => pattern.test(norm))
    );
    if (rule) return ruleReply(rule, plants);
    if (plants.length) return answer({ plants: plants.slice(0, 1), intents }, proc);

    const intentWords = [...kb.intentWords];
    const leftover = tokens.filter(token => {
      if (kb.domain.terms.some(term => token.startsWith(term))) return false;
      return !intentWords.some(word =>
        (word.length >= 4 && token.includes(word)) ||
        (token.length >= 4 && word.includes(token)) ||
        token === word
      );
    });
    if (intents.length && !leftover.length) {
      if (lastPlant) return answer({ plants: [lastPlant], intents }, proc);
      return { kind: "clarify" };
    }
    if (intents.length) return { kind: "no_match" };
    if (tokens.some(token => kb.domain.terms.some(term => token.startsWith(term)))) {
      return { kind: "no_match" };
    }
    return { kind: "offtopic" };
  }

  function reply(text) {
    const now = Date.now();
    stamps = stamps.filter(stamp => now - stamp < 60_000);
    if (stamps.length >= kb.settings.rate_limit_per_minute) {
      return kb.messages.rate_limited;
    }
    stamps.push(now);
    const proc = processInput(text);
    if (proc.empty) return kb.messages.empty;
    const result = classify(proc);
    let response;
    if (result.text) response = result.text;
    else if (result.kind === "offtopic") response = kb.messages.offtopic;
    else if (result.kind === "out_of_scope") {
      response = kb.messages.out_of_scope
        .replace("{term}", result.term)
        .replace("{daftar}", plantList());
    } else if (result.kind === "clarify") {
      response = kb.messages.clarify.replace("{daftar}", plantList());
    } else {
      try {
        const unanswered = JSON.parse(localStorage.getItem("tumbuhan-tropis-unanswered") || "[]");
        unanswered.push({ at: new Date().toISOString(), text: proc.raw.slice(0, 200) });
        localStorage.setItem("tumbuhan-tropis-unanswered", JSON.stringify(unanswered.slice(-100)));
      } catch (error) {
        console.warn("Pertanyaan belum terjawab tidak dapat disimpan di perangkat.", error);
      }
      response = kb.messages.no_match.replace("{daftar}", plantList()) +
        "\n\nChatbot ini berjalan tanpa petugas atau handoff. Pertanyaan hanya tersimpan di perangkat ini.";
    }

    try {
      sessionStorage.setItem("tumbuhan-tropis-session", JSON.stringify({
        lastPlant,
        asked: [...asked],
        stamps
      }));
    } catch (error) {
      console.warn("Konteks sesi chatbot tidak dapat disimpan.", error);
    }
    return response;
  }

  async function send(text) {
    add("user", text);
    sendBtn.disabled = true;
    chips.forEach(button => { button.disabled = true; });
    status.textContent = "Mencari jawaban di basis data lokal...";
    try {
      const response = reply(text);
      add("bot", response);
      status.textContent = "";
    } catch (error) {
      console.error("Gagal memproses pertanyaan dari basis data lokal.", error);
      add("bot", "Maaf, basis data lokal gagal diproses. Muat ulang halaman atau periksa file tropical_plants_kb.json.");
      status.textContent = "Chatbot lokal mengalami kesalahan.";
    } finally {
      sendBtn.disabled = false;
      chips.forEach(button => { button.disabled = false; });
      input.focus();
    }
  }

  form.addEventListener("submit", event => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    send(text);
  });
  chips.forEach(button => button.addEventListener("click", () => {
    if (!sendBtn.disabled) send(button.textContent);
  }));

  fetch("/tropical_plants_kb.json")
    .then(response => {
      if (!response.ok) throw new Error(`Gagal memuat basis pengetahuan (${response.status}).`);
      return response.json();
    })
    .then(data => {
      kb = prepareKnowledge(data);
      add("bot", "Halo! Saya asisten pengetahuan tumbuhan tropis. Tanyakan manfaat, ciri, habitat, atau cara merawat tanaman tropis.");
      status.textContent = "";
      sendBtn.disabled = false;
      chips.forEach(button => { button.disabled = false; });
    })
    .catch(error => {
      console.error("Basis pengetahuan chatbot lokal gagal dimuat.", error);
      status.textContent = "Basis data lokal tidak dapat dimuat. Pastikan menjalankan server.py dari folder proyek.";
      sendBtn.disabled = true;
      chips.forEach(button => { button.disabled = true; });
    });
})();
