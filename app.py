from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from supabase import create_client
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = "sua_chave_secreta"

# SUA NOVA CONFIGURAÇÃO DO SUPABASE - SUBSTITUA PELOS SEUS DADOS
SUPABASE_URL = "https://pnpybnpbqwiteocpbcbb.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InBucHlibnBicXdpdGVvY3BiY2JiIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzQwMDU0ODIsImV4cCI6MjA4OTU4MTQ4Mn0.LkBufgdceo1Qijj06g0dY2TyQmT7bOQSR9nPVpFUKm8"
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Categorias de materiais esportivos
CATEGORIAS = [
    "Bolas", "Redes", "Uniformes", "Chuteiras", "Tênis", 
    "Luvas", "Caneleiras", "Apitos", "Cones", "Bastões",
    "Raquetes", "Petanque", "Volantes", "Tatames", "Outros"
]

# ----------------------------
# FUNÇÕES AUXILIARES
# ----------------------------
def calcular_disponiveis(material):
    """Calcula quantos itens estão disponíveis"""
    total = material.get("quantidade_total") or 0
    emprestimos_count = len(material.get("emprestimos", []))
    return total - emprestimos_count

def get_materiais_com_disponiveis():
    """Busca todos materiais com contagem de empréstimos"""
    query = supabase.table("materiais").select("*, emprestimos(id)").execute()
    materiais = query.data
    for material in materiais:
        material["disponiveis"] = calcular_disponiveis(material)
    return materiais

def get_totais():
    """Calcula totais de materiais e empréstimos"""
    materiais = supabase.table("materiais").select("id,quantidade_total,emprestimos(id)").execute().data
    total_materiais = sum(m.get("quantidade_total") or 0 for m in materiais)
    total_emprestados = sum(len(m.get("emprestimos", [])) for m in materiais)
    total_reservados = supabase.table("reservas").select("id").execute().count or 0
    return total_materiais, total_emprestados, total_reservados

# ----------------------------
# ROTA PRINCIPAL
# ----------------------------
@app.route("/")
def index():
    materiais = get_materiais_com_disponiveis()
    total_materiais, total_emprestados, total_reservados = get_totais()
    return render_template(
        "index.html",
        materiais=materiais,
        total_materiais=total_materiais,
        total_emprestados=total_emprestados,
        total_reservados=total_reservados,
        categorias=CATEGORIAS
    )

# ----------------------------
# CADASTRAR MATERIAL
# ----------------------------
@app.route("/cadastrar", methods=["GET", "POST"])
def cadastrar():
    if request.method == "POST":
        nome = request.form["nome"].strip()
        categoria = request.form["categoria"]
        quantidade = int(request.form["quantidade"])
        especificacoes = request.form.get("especificacoes", "").strip()

        if quantidade < 1:
            flash("O material precisa ter pelo menos 1 unidade.", "erro")
            return redirect(url_for("cadastrar"))

        # Verificar se material já existe
        existente = supabase.table("materiais")\
            .select("id")\
            .eq("nome", nome).eq("categoria", categoria)\
            .execute().data

        if existente:
            flash(f"Material '{nome}' já está cadastrado nesta categoria.", "erro")
            return redirect(url_for("index"))

        # Inserir novo material
        supabase.table("materiais").insert({
            "nome": nome,
            "categoria": categoria,
            "quantidade_total": quantidade,
            "especificacoes": especificacoes,
            "data_aquisicao": datetime.now().strftime("%Y-%m-%d")
        }).execute()

        flash(f"Material '{nome}' cadastrado com sucesso!", "sucesso")
        return redirect(url_for("index"))

    return render_template("cadastrar.html", categorias=CATEGORIAS)

# ----------------------------
# EMPRESTAR MATERIAL
# ----------------------------
@app.route("/emprestar/<int:material_id>", methods=["GET", "POST"])
def emprestar(material_id):
    turmas = ["6ºA","6ºB","6ºC","6ºD","6ºE","7ºA","7ºB","7ºC","7ºD",
              "8ºA","8ºB","8ºC","9ºA","9ºB","9ºC","9ºD"]

    material = supabase.table("materiais").select("*, emprestimos(id)").eq("id", material_id).single().execute().data
    if not material:
        flash("Material não encontrado.", "erro")
        return redirect(url_for("index"))

    material["disponiveis"] = calcular_disponiveis(material)

    if request.method == "POST":
        aluno = request.form["aluno"].strip()
        turma_index = int(request.form.get("turma", 1)) - 1
        turma_index = max(0, min(turma_index, len(turmas)-1))
        turma = turmas[turma_index]
        
        # Data de devolução prevista (7 dias)
        data_devolucao_prevista = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

        if material["disponiveis"] <= 0:
            # Se não tem disponível, faz reserva
            supabase.table("reservas").insert({
                "material_id": material_id,
                "aluno": aluno,
                "turma": turma,
                "data_reserva": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }).execute()
            flash(f"Não há unidades disponíveis. '{material['nome']}' foi reservado!", "erro")
        else:
            # Faz o empréstimo
            supabase.table("emprestimos").insert({
                "material_id": material_id,
                "aluno": aluno,
                "turma": turma,
                "data_emprestimo": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "data_devolucao_prevista": data_devolucao_prevista
            }).execute()
            flash(f"Material '{material['nome']}' emprestado com sucesso para {aluno}!", "sucesso")

        return redirect(url_for("index"))

    return render_template("emprestar.html", material=material, turmas=turmas)

# ----------------------------
# DEVOLVER MATERIAL
# ----------------------------
@app.route("/devolver/<int:emprestimo_id>", methods=["POST"])
def devolver(emprestimo_id):
    emprestimo = supabase.table("emprestimos").select("*").eq("id", emprestimo_id).single().execute().data
    if not emprestimo:
        return redirect(url_for("index"))

    material_id = emprestimo["material_id"]
    
    # Registrar devolução
    supabase.table("emprestimos").update({
        "data_devolucao_real": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }).eq("id", emprestimo_id).execute()

    # Verificar se há reservas para este material
    reservas = supabase.table("reservas").select("*").eq("material_id", material_id)\
        .order("data_reserva").limit(1).execute().data
    
    if reservas:
        primeira = reservas[0]
        data_devolucao_prevista = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        
        # Converter reserva em empréstimo
        supabase.table("emprestimos").insert({
            "material_id": material_id,
            "aluno": primeira["aluno"],
            "turma": primeira["turma"],
            "data_emprestimo": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data_devolucao_prevista": data_devolucao_prevista
        }).execute()
        
        # Remover reserva
        supabase.table("reservas").delete().eq("id", primeira["id"]).execute()
        flash(f"Material devolvido e repassado para o aluno reservista: {primeira['aluno']}", "sucesso")
    else:
        flash("Material devolvido com sucesso!", "sucesso")

    return redirect(url_for("index"))

# ----------------------------
# EXCLUIR MATERIAL
# ----------------------------
@app.route("/excluir/<int:material_id>", methods=["POST"])
def excluir(material_id):
    supabase.table("materiais").delete().eq("id", material_id).execute()
    flash("Material excluído com sucesso!", "sucesso")
    return redirect(url_for("index"))

# ----------------------------
# ATUALIZAR MATERIAL
# ----------------------------
@app.route("/atualizar/<int:material_id>", methods=["GET", "POST"])
def atualizar(material_id):
    material = supabase.table("materiais").select("*").eq("id", material_id).single().execute().data
    if not material:
        return redirect(url_for("index"))

    if request.method == "POST":
        supabase.table("materiais").update({
            "nome": request.form["nome"],
            "categoria": request.form["categoria"],
            "quantidade_total": int(request.form["quantidade"]),
            "especificacoes": request.form.get("especificacoes", "")
        }).eq("id", material_id).execute()
        flash(f"Material '{material['nome']}' atualizado com sucesso!", "sucesso")
        return redirect(url_for("index"))

    return render_template("atualizar.html", material=material, categorias=CATEGORIAS)

# ----------------------------
# BUSCAR / AUTOCOMPLETE
# ----------------------------
@app.route("/buscar")
def buscar():
    termo = request.args.get("q", "").strip()
    if not termo:
        return redirect(url_for("index"))

    materiais = supabase.table("materiais").select("*, emprestimos(id)")\
        .filter("nome", "ilike", f"%{termo}%")\
        .execute().data
    
    # Buscar por categoria também
    materiais_categoria = supabase.table("materiais").select("*, emprestimos(id)")\
        .filter("categoria", "ilike", f"%{termo}%")\
        .execute().data

    ids_existentes = {m["id"] for m in materiais}
    for m in materiais_categoria:
        if m["id"] not in ids_existentes:
            materiais.append(m)

    for material in materiais:
        material["disponiveis"] = calcular_disponiveis(material)

    total_materiais = sum(m.get("quantidade_total") or 0 for m in materiais)
    total_emprestados = sum(len(m.get("emprestimos", [])) for m in materiais)

    return render_template("index.html",
                           materiais=materiais,
                           termo=termo,
                           total_materiais=total_materiais,
                           total_emprestados=total_emprestados,
                           categorias=CATEGORIAS)

@app.route("/autocomplete")
def autocomplete():
    termo = request.args.get("q", "")
    if not termo:
        return jsonify([])

    materiais = supabase.table("materiais").select("nome")\
        .ilike("nome", f"%{termo}%").limit(10).execute().data
    sugestoes = [m["nome"] for m in materiais]
    return jsonify(sugestoes)

# ----------------------------
# LISTAR EMPRÉSTIMOS ATIVOS
# ----------------------------
@app.route("/emprestimos_ativos")
def emprestimos_ativos():
    materiais = supabase.table("materiais").select("id,quantidade_total").execute().data
    total_materiais = sum(m.get("quantidade_total") or 0 for m in materiais)

    # Buscar apenas empréstimos que não foram devolvidos
    emprestimos = supabase.table("emprestimos").select("*, materiais(*)")\
        .is_("data_devolucao_real", "null").execute().data
    total_emprestados = len(emprestimos)

    return render_template("emprestimos_ativos.html",
                           emprestimos=emprestimos,
                           total_materiais=total_materiais,
                           total_emprestados=total_emprestados)

@app.route("/reservas")
def reservas():
    reservas_list = supabase.table("reservas").select("*, materiais(*)")\
        .order("data_reserva").execute().data
    total_reservas = len(reservas_list)
    return render_template("reservas.html", reservas=reservas_list, total_reservas=total_reservas)

if __name__ == "__main__":
    app.run(debug=True)