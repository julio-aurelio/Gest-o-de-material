import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from supabase import create_client
from datetime import datetime, timedelta

app = Flask(__name__)

# ==================== CONFIGURAÇÕES ====================
app.secret_key = "sua_chave_secreta_aqui_mude_isso_para_algo_seguro"

SUPABASE_URL = "https://pnpybnpbqwiteocpbcbb.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InBucHlibnBicXdpdGVvY3BiY2JiIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzQwMDU0ODIsImV4cCI6MjA4OTU4MTQ4Mn0.LkBufgdceo1Qijj06g0dY2TyQmT7bOQSR9nPVpFUKm8"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==================== CATEGORIAS ====================
CATEGORIAS = [
    "Esporte", "Educacional", "Outros"
]

HORARIOS = [
    "1ª Aula",
    "2ª Aula", 
    "3ª Aula",
    "4ª Aula",
    "5ª Aula"
]

TURMAS_MANHA = ["8ºA", "8ºB", "8ºC", "8ºD", "9ºA", "9ºB", "9ºC", "9ºD"]
TURMAS_TARDE = ["6ºA", "6ºB", "6ºC", "6ºD", "6ºE", "7ºA", "7ºB", "7ºC"]

# ==================== FUNÇÕES AUXILIARES ====================
def get_turno_by_turma(turma):
    if turma in TURMAS_MANHA:
        return "Manhã"
    elif turma in TURMAS_TARDE:
        return "Tarde"
    return "Manhã"

def verificar_conflito_horario(material_id, data, turno, horario, reserva_id=None):
    """
    Verifica se já existe reserva ou empréstimo para o mesmo 
    (data + turno + horário)
    """
    # Verificar empréstimos ativos para essa data e horário
    emprestimos = supabase.table("emprestimos")\
        .select("id")\
        .eq("material_id", material_id)\
        .eq("data_emprestimo_data", data)\
        .eq("turno", turno)\
        .eq("horario", horario)\
        .is_("data_devolucao_real", "null")\
        .execute().data
    
    if emprestimos:
        return True, "Já existe um empréstimo para este horário nesta data."
    
    # Verificar reservas para essa data e horário
    query = supabase.table("reservas")\
        .select("id")\
        .eq("material_id", material_id)\
        .eq("data_retirada", data)\
        .eq("turno", turno)\
        .eq("horario", horario)
    
    # Se for atualização, ignorar a própria reserva
    if reserva_id:
        query = query.neq("id", reserva_id)
    
    reservas = query.execute().data
    
    if reservas:
        return True, "Já existe uma reserva para este horário nesta data."
    
    return False, ""

def verificar_disponibilidade_quantidade(material_id, data, turno, horario, quantidade_solicitada):
    """Verifica se há quantidade suficiente disponível para a data"""
    material = supabase.table("materiais").select("quantidade_total").eq("id", material_id).single().execute().data
    if not material:
        return False, 0
    
    total = material["quantidade_total"]
    
    # Buscar empréstimos para a data específica
    emprestimos = supabase.table("emprestimos")\
        .select("quantidade_emprestada")\
        .eq("material_id", material_id)\
        .eq("data_emprestimo_data", data)\
        .eq("turno", turno)\
        .eq("horario", horario)\
        .is_("data_devolucao_real", "null")\
        .execute().data
    
    total_emprestado = sum(e.get("quantidade_emprestada", 1) for e in emprestimos)
    
    # Buscar reservas para a data específica
    reservas = supabase.table("reservas")\
        .select("quantidade_reservada")\
        .eq("material_id", material_id)\
        .eq("data_retirada", data)\
        .eq("turno", turno)\
        .eq("horario", horario)\
        .execute().data
    
    total_reservado = sum(r.get("quantidade_reservada", 1) for r in reservas)
    
    disponivel = total - total_emprestado - total_reservado
    return disponivel >= quantidade_solicitada, disponivel

def calcular_disponiveis_hoje(material_id, turno):
    """Calcula disponibilidade para hoje (apenas empréstimos ativos)"""
    material = supabase.table("materiais").select("quantidade_total").eq("id", material_id).single().execute().data
    if not material:
        return 0
    
    total = material["quantidade_total"]
    hoje = datetime.now().strftime("%Y-%m-%d")
    
    # Empréstimos ativos de hoje
    emprestimos_ativos = supabase.table("emprestimos")\
        .select("quantidade_emprestada")\
        .eq("material_id", material_id)\
        .eq("data_emprestimo_data", hoje)\
        .eq("turno", turno)\
        .is_("data_devolucao_real", "null")\
        .execute().data
    
    total_emprestado = sum(e.get("quantidade_emprestada", 1) for e in emprestimos_ativos)
    
    return total - total_emprestado

def get_materiais_com_disponiveis():
    """Busca materiais com disponibilidade para HOJE"""
    materiais = supabase.table("materiais").select("*").execute().data
    hoje = datetime.now().strftime("%Y-%m-%d")
    
    for material in materiais:
        material["disponiveis_manha"] = calcular_disponiveis_hoje(material["id"], "Manhã")
        material["disponiveis_tarde"] = calcular_disponiveis_hoje(material["id"], "Tarde")
        material["total"] = material["quantidade_total"]
        material["disponiveis"] = material["disponiveis_manha"] + material["disponiveis_tarde"]
    
    return materiais

def get_totais():
    materiais = supabase.table("materiais").select("quantidade_total").execute()
    total_materiais = sum(m.get("quantidade_total", 0) for m in materiais.data)
    
    emprestimos = supabase.table("emprestimos").select("id").is_("data_devolucao_real", "null").execute()
    total_emprestados = len(emprestimos.data)
    
    reservas = supabase.table("reservas").select("id").execute()
    total_reservados = len(reservas.data)
    
    return total_materiais, total_emprestados, total_reservados

# ==================== ROTA PRINCIPAL ====================
@app.route("/")
def index():
    try:
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
    except Exception as e:
        return f"Erro ao carregar dados: {str(e)}", 500

# ==================== CADASTRAR MATERIAL ====================
@app.route("/cadastrar", methods=["GET", "POST"])
def cadastrar():
    if request.method == "POST":
        nome = request.form["nome"].strip()
        categoria = request.form["categoria"]
        quantidade = int(request.form["quantidade"])
        especificacoes = request.form.get("especificacoes", "").strip()

        if quantidade < 1:
            flash("O material precisa ter pelo menos 1 unidade.", "error")
            return redirect(url_for("cadastrar"))

        existente = supabase.table("materiais")\
            .select("id")\
            .eq("nome", nome).eq("categoria", categoria)\
            .execute().data

        if existente:
            flash(f"Material '{nome}' já está cadastrado nesta categoria.", "error")
            return redirect(url_for("index"))

        supabase.table("materiais").insert({
            "nome": nome,
            "categoria": categoria,
            "quantidade_total": quantidade,
            "especificacoes": especificacoes,
            "data_aquisicao": datetime.now().strftime("%Y-%m-%d")
        }).execute()

        flash(f"Material '{nome}' cadastrado com sucesso!", "success")
        return redirect(url_for("index"))

    return render_template("cadastrar.html", categorias=CATEGORIAS)

# ==================== EMPRESTAR/RESERVAR MATERIAL ====================
@app.route("/emprestar/<int:material_id>", methods=["GET", "POST"])
def emprestar(material_id):
    material = supabase.table("materiais").select("*").eq("id", material_id).single().execute().data
    if not material:
        flash("Material não encontrado.", "error")
        return redirect(url_for("index"))

    if request.method == "POST":
        aluno = request.form["aluno"].strip()
        turma = request.form["turma"]
        horario_index = int(request.form.get("horario", 0))
        horario = HORARIOS[horario_index] if horario_index < len(HORARIOS) else HORARIOS[0]
        quantidade = int(request.form.get("quantidade", 1))
        data_retirada = request.form.get("data_retirada", datetime.now().strftime("%Y-%m-%d"))
        
        turno = get_turno_by_turma(turma)
        
        # VALIDAÇÃO 1: Verificar conflito de horário (data + turno + horário)
        tem_conflito, msg_conflito = verificar_conflito_horario(material_id, data_retirada, turno, horario)
        if tem_conflito:
            flash(msg_conflito, "error")
            return redirect(url_for("emprestar", material_id=material_id))
        
        # VALIDAÇÃO 2: Verificar quantidade disponível
        tem_quantidade, disponivel = verificar_disponibilidade_quantidade(
            material_id, data_retirada, turno, horario, quantidade
        )
        
        if not tem_quantidade:
            flash(f"Apenas {disponivel} unidades disponíveis para {turno.lower()} no dia {data_retirada} no horário {horario}.", "error")
            return redirect(url_for("emprestar", material_id=material_id))
        
        if quantidade <= 0:
            flash("Quantidade inválida.", "error")
            return redirect(url_for("emprestar", material_id=material_id))
        
        data_devolucao_prevista = (datetime.strptime(data_retirada, "%Y-%m-%d") + timedelta(days=7)).strftime("%Y-%m-%d")
        
        # Se for hoje, empresta imediatamente
        if data_retirada == datetime.now().strftime("%Y-%m-%d"):
            supabase.table("emprestimos").insert({
                "material_id": material_id,
                "aluno": aluno,
                "turma": turma,
                "turno": turno,
                "horario": horario,
                "quantidade_emprestada": quantidade,
                "data_emprestimo": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "data_emprestimo_data": data_retirada,
                "data_devolucao_prevista": data_devolucao_prevista
            }).execute()
            flash(f"{quantidade}x '{material['nome']}' EMPRESTADO para {aluno} ({turma}) no horário {horario}!", "success")
        else:
            # Reserva para data futura
            supabase.table("reservas").insert({
                "material_id": material_id,
                "aluno": aluno,
                "turma": turma,
                "turno": turno,
                "horario": horario,
                "quantidade_reservada": quantidade,
                "data_reserva": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "data_retirada": data_retirada
            }).execute()
            flash(f"{quantidade}x '{material['nome']}' RESERVADO para {aluno} ({turma}) no horário {horario} para o dia {data_retirada}!", "warning")

        return redirect(url_for("index"))
    
    # GET - mostrar formulário
    disponiveis_manha = calcular_disponiveis_hoje(material_id, "Manhã")
    disponiveis_tarde = calcular_disponiveis_hoje(material_id, "Tarde")
    
    return render_template("emprestar.html", 
                         material=material, 
                         turmas_manha=TURMAS_MANHA,
                         turmas_tarde=TURMAS_TARDE,
                         horarios=HORARIOS,
                         disponiveis_manha=disponiveis_manha,
                         disponiveis_tarde=disponiveis_tarde,
                         hoje=datetime.now().strftime("%Y-%m-%d"))

# ==================== ATUALIZAR RESERVA ====================
@app.route("/atualizar_reserva/<int:reserva_id>", methods=["GET", "POST"])
def atualizar_reserva(reserva_id):
    reserva = supabase.table("reservas").select("*, materiais(*)").eq("id", reserva_id).single().execute().data
    if not reserva:
        flash("Reserva não encontrada.", "error")
        return redirect(url_for("reservas"))

    if request.method == "POST":
        nova_data = request.form.get("data_retirada")
        novo_horario_index = int(request.form.get("horario", 0))
        novo_horario = HORARIOS[novo_horario_index] if novo_horario_index < len(HORARIOS) else HORARIOS[0]
        
        # VALIDAÇÃO: Verificar conflito de horário (ignorando a própria reserva)
        tem_conflito, msg_conflito = verificar_conflito_horario(
            reserva["material_id"], nova_data, reserva["turno"], novo_horario, reserva_id
        )
        
        if tem_conflito:
            flash(msg_conflito, "error")
            return redirect(url_for("atualizar_reserva", reserva_id=reserva_id))
        
        # Verificar disponibilidade de quantidade para a nova data
        tem_quantidade, disponivel = verificar_disponibilidade_quantidade(
            reserva["material_id"], nova_data, reserva["turno"], novo_horario, reserva["quantidade_reservada"]
        )
        
        if not tem_quantidade:
            flash(f"Apenas {disponivel} unidades disponíveis para {reserva['turno'].lower()} no dia {nova_data} no horário {novo_horario}.", "error")
            return redirect(url_for("atualizar_reserva", reserva_id=reserva_id))
        
        # Atualizar reserva
        supabase.table("reservas").update({
            "data_retirada": nova_data,
            "horario": novo_horario
        }).eq("id", reserva_id).execute()
        
        flash(f"Reserva atualizada para o dia {nova_data} no horário {novo_horario}!", "success")
        return redirect(url_for("reservas"))
    
    return render_template("atualizar_reserva.html", reserva=reserva, horarios=HORARIOS, hoje=datetime.now().strftime("%Y-%m-%d"))

# ==================== CANCELAR RESERVA ====================
@app.route("/cancelar_reserva/<int:reserva_id>", methods=["POST"])
def cancelar_reserva(reserva_id):
    try:
        supabase.table("reservas").delete().eq("id", reserva_id).execute()
        flash("Reserva cancelada com sucesso!", "success")
    except Exception as e:
        flash(f"Erro ao cancelar reserva: {str(e)}", "error")
    
    return redirect(url_for("reservas"))

# ==================== DEVOLVER MATERIAL ====================
@app.route("/devolver/<int:emprestimo_id>", methods=["POST"])
def devolver(emprestimo_id):
    try:
        emprestimo = supabase.table("emprestimos")\
            .select("*")\
            .eq("id", emprestimo_id)\
            .single()\
            .execute()
        
        if not emprestimo.data:
            flash("Empréstimo não encontrado.", "error")
            return redirect(url_for("index"))

        material_id = emprestimo.data["material_id"]
        turno = emprestimo.data["turno"]
        horario = emprestimo.data["horario"]
        
        # Registrar devolução
        supabase.table("emprestimos")\
            .update({
                "data_devolucao_real": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })\
            .eq("id", emprestimo_id)\
            .execute()

        # Verificar se há reservas para hoje neste mesmo horário
        hoje = datetime.now().strftime("%Y-%m-%d")
        reservas = supabase.table("reservas")\
            .select("*")\
            .eq("material_id", material_id)\
            .eq("turno", turno)\
            .eq("horario", horario)\
            .eq("data_retirada", hoje)\
            .order("data_reserva")\
            .limit(1)\
            .execute()
        
        if reservas.data:
            primeira = reservas.data[0]
            data_devolucao_prevista = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
            
            supabase.table("emprestimos").insert({
                "material_id": material_id,
                "aluno": primeira["aluno"],
                "turma": primeira["turma"],
                "turno": primeira["turno"],
                "horario": primeira["horario"],
                "quantidade_emprestada": primeira["quantidade_reservada"],
                "data_emprestimo": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "data_emprestimo_data": hoje,
                "data_devolucao_prevista": data_devolucao_prevista
            }).execute()
            
            supabase.table("reservas").delete().eq("id", primeira["id"]).execute()
            flash(f"Material devolvido e repassado para: {primeira['aluno']} ({primeira['quantidade_reservada']}x)", "success")
        else:
            flash("Material devolvido com sucesso!", "success")
            
    except Exception as e:
        flash(f"Erro na devolução: {str(e)}", "error")
    
    return redirect(url_for("index"))

# ==================== EXCLUIR MATERIAL ====================
@app.route("/excluir/<int:material_id>", methods=["POST"])
def excluir(material_id):
    supabase.table("materiais").delete().eq("id", material_id).execute()
    flash("Material excluído com sucesso!", "success")
    return redirect(url_for("index"))

# ==================== ATUALIZAR MATERIAL ====================
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
        flash(f"Material '{material['nome']}' atualizado com sucesso!", "success")
        return redirect(url_for("index"))

    return render_template("atualizar.html", material=material, categorias=CATEGORIAS)

# ==================== BUSCAR / AUTOCOMPLETE ====================
@app.route("/buscar")
def buscar():
    termo = request.args.get("q", "").strip()
    if not termo:
        return redirect(url_for("index"))

    materiais = supabase.table("materiais").select("*")\
        .filter("nome", "ilike", f"%{termo}%")\
        .execute().data
    
    materiais_categoria = supabase.table("materiais").select("*")\
        .filter("categoria", "ilike", f"%{termo}%")\
        .execute().data

    ids_existentes = {m["id"] for m in materiais}
    for m in materiais_categoria:
        if m["id"] not in ids_existentes:
            materiais.append(m)

    for material in materiais:
        material["disponiveis_manha"] = calcular_disponiveis_hoje(material["id"], "Manhã")
        material["disponiveis_tarde"] = calcular_disponiveis_hoje(material["id"], "Tarde")
        material["disponiveis"] = material["disponiveis_manha"] + material["disponiveis_tarde"]

    total_materiais = sum(m.get("quantidade_total") or 0 for m in materiais)
    
    total_emprestados = 0
    for material in materiais:
        emprestimos = supabase.table("emprestimos").select("id")\
            .eq("material_id", material["id"])\
            .is_("data_devolucao_real", "null")\
            .execute().data
        total_emprestados += len(emprestimos)

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

# ==================== LISTAR EMPRÉSTIMOS ATIVOS ====================
@app.route("/emprestimos_ativos")
def emprestimos_ativos():
    materiais = supabase.table("materiais").select("id,quantidade_total").execute().data
    total_materiais = sum(m.get("quantidade_total") or 0 for m in materiais)

    emprestimos = supabase.table("emprestimos").select("*, materiais(*)")\
        .is_("data_devolucao_real", "null")\
        .order("data_emprestimo")\
        .execute().data
    total_emprestados = len(emprestimos)

    return render_template("emprestimos_ativos.html",
                           emprestimos=emprestimos,
                           total_materiais=total_materiais,
                           total_emprestados=total_emprestados)

# ==================== LISTAR RESERVAS ====================
@app.route("/reservas")
def reservas():
    reservas_list = supabase.table("reservas").select("*, materiais(*)")\
        .order("data_retirada")\
        .execute().data
    total_reservas = len(reservas_list)
    return render_template("reservas.html", reservas=reservas_list, total_reservas=total_reservas)

# ==================== PROCESSAR RESERVAS DO DIA ====================
@app.route("/processar_reservas")
def processar_reservas():
    """Processa reservas cuja data de retirada é hoje"""
    try:
        hoje = datetime.now().strftime("%Y-%m-%d")
        
        reservas_hoje = supabase.table("reservas")\
            .select("*")\
            .eq("data_retirada", hoje)\
            .execute().data
        
        for reserva in reservas_hoje:
            # Verificar conflito de horário
            tem_conflito, _ = verificar_conflito_horario(
                reserva["material_id"], hoje, reserva["turno"], reserva["horario"]
            )
            
            if tem_conflito:
                amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
                supabase.table("reservas").update({
                    "data_retirada": amanha
                }).eq("id", reserva["id"]).execute()
                flash(f"Reserva para {reserva['aluno']} foi adiada para amanhã devido a conflito de horário.", "warning")
                continue
            
            # Verificar quantidade disponível
            tem_quantidade, disponivel = verificar_disponibilidade_quantidade(
                reserva["material_id"], hoje, reserva["turno"], reserva["horario"], reserva["quantidade_reservada"]
            )
            
            if tem_quantidade:
                data_devolucao_prevista = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
                
                supabase.table("emprestimos").insert({
                    "material_id": reserva["material_id"],
                    "aluno": reserva["aluno"],
                    "turma": reserva["turma"],
                    "turno": reserva["turno"],
                    "horario": reserva["horario"],
                    "quantidade_emprestada": reserva["quantidade_reservada"],
                    "data_emprestimo": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "data_emprestimo_data": hoje,
                    "data_devolucao_prevista": data_devolucao_prevista
                }).execute()
                
                supabase.table("reservas").delete().eq("id", reserva["id"]).execute()
                flash(f"Reserva para {reserva['aluno']} ({reserva['quantidade_reservada']}x) foi processada com sucesso!", "success")
            else:
                amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
                supabase.table("reservas").update({
                    "data_retirada": amanha
                }).eq("id", reserva["id"]).execute()
                flash(f"Reserva para {reserva['aluno']} foi adiada para amanhã devido à falta de material.", "warning")
        
        return redirect(url_for("index"))
    except Exception as e:
        flash(f"Erro ao processar reservas: {str(e)}", "error")
        return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True)