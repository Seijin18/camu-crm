"""CRM de conversas da Camu.

Acompanha as conversas de WhatsApp para converter mais vendas B2B e B2C.

A divisão estruturante (§1 de `docs/04-crm-conversas-definicoes.md`):

    LLM extrai fatos  ->  regra determinística decide  ->  humano envia

O LLM nunca decide estágio, temperatura, prioridade ou envio. Ele responde
perguntas factuais fechadas, com evidência literal obrigatória.
"""

__version__ = "0.1.0"
