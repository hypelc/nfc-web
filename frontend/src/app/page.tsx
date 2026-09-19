"use client";

import { FormEvent, useState } from "react";
import Image from "next/image";
import { useRouter } from "next/navigation";

import { supabase } from "@/lib/supabase";

import ThemeToggle from "./components/ThemeToggle";
import styles from "./page.module.css";

export default function Home() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const [mensagem, setMensagem] = useState("");
  const [carregando, setCarregando] = useState(false);

  async function entrar(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMensagem("");
    setCarregando(true);

    const { error } = await supabase.auth.signInWithPassword({
      email,
      password: senha,
    });

    if (error) {
      setMensagem("E-mail ou senha inválidos.");
    } else {
      router.push("/dashboard");
    }

    setCarregando(false);
  }

  return (
    <main className={styles.page}>
      <section className={styles.introduction} aria-label="Sobre o NL NFC">
        <div className={styles.introductionHeader}>
          <div className={styles.brand}>
            <Image
              className={styles.brandLogo}
              src="/brand/nl-white-transparent.png"
              alt="NL"
              width={490}
              height={212}
              priority
            />
            <span>NFC</span>
          </div>
          <ThemeToggle variant="onIntro" />
        </div>

        <div className={styles.introductionContent}>
          <p className={styles.eyebrow}>Controle de avaliações</p>
          <h1>Veja o que acontece depois de cada leitura.</h1>
          <p className={styles.description}>
            Acompanhe acessos por QR Code e NFC em um único painel, com dados
            claros para cada estabelecimento.
          </p>
        </div>

        <p className={styles.footerNote}>Acesso exclusivo para clientes</p>
      </section>

      <section className={styles.loginArea}>
        <form className={styles.loginCard} onSubmit={entrar}>
          <div>
            <p className={styles.eyebrow}>Área do cliente</p>
            <h2>Entrar no painel</h2>
            <p className={styles.formDescription}>
              Use o e-mail e a senha cadastrados para acessar suas estatísticas.
            </p>
          </div>

          <label htmlFor="email">
            E-mail
            <input
              id="email"
              name="email"
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </label>

          <label htmlFor="senha">
            Senha
            <input
              id="senha"
              name="senha"
              type="password"
              autoComplete="current-password"
              value={senha}
              onChange={(event) => setSenha(event.target.value)}
              required
            />
          </label>

          <button type="submit" disabled={carregando}>
            {carregando ? "Entrando..." : "Entrar"}
          </button>

          {mensagem && (
            <p className={styles.message} role="status">
              {mensagem}
            </p>
          )}
        </form>
      </section>
    </main>
  );
}
