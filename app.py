import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from supabase import create_client
from datetime import datetime, timedelta
from functools import wraps

app = Flask(__name__)
app.secret_key = "sua_chave_secreta_aqui_mude_isso_para_algo_seguro"

SUPABASE_URL = "https://pnpybnpbqwiteocpbcbb.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InBucHlibnBicXdpdGVvY3BiY2JiIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzQwMDU0ODIsImV4cCI6MjA4OTU4MTQ4Mn0.LkBufgdceo1Qijj06g0dY2TyQmT7bOQSR9nPVpFUKm8"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==================== CACHE OTIMIZADO ====================
class SimpleCache:
    def __init__(self, timeout=600):
        self.cache = {}
        self.timeout = timeout
    
    def get(self, key):
        if key in self.cache:
            data, timestamp = self.cache[key]
            if datetime.now().timestamp() - timestamp < self.timeout:
                return data
            del self.cache[key]
        return None
    
    def set(self, key, value):
        self.cache[key] = (value, datetime.now().timestamp())
    
    def clear(self):
        self.cache.clear()

cache = SimpleCache(timeout=600)

# ==================== DECORADORES ====================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario_id' not in session:
            flash("Por favor, faça login para acessar esta página.", "warning")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario_id' not in session:
            flash("Por favor, faça login para acessar esta página.", "warning")
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            flash("Acesso negado. Você não tem permissão de administrador.", "error")
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def pode_editar_reserva(reserva_id, usuario_id, role):
    if role == 'admin':
        return True
    reserva = supabase.table("reservas").select("usuario_id").eq("id", reserva_id).single().execute().data
    return reserva and reserva.get("usuario_id") == usuario_id

def pode_devolver_emprestimo(emprestimo_id, usuario_id, role):
    if role == 'admin':
        return True
    emprestimo = supabase.table("emprestimos").select("usuario_id").eq("id", emprestimo_id).single().execute().data
    return emprestimo and emprestimo.get("usuario_id") == usuario_id

# ==================== CONFIGURAÇÕES ====================
CATEGORIAS = ["Esporte", "Educacional", "Outros"]
HORARIOS = ["1ª Aula", "2ª Aula", "3ª Aula", "4ª Aula", "5ª Aula"]
TURMAS_MANHA = ["8ºA", "8ºB", "8ºC", "8ºD", "9ºA", "9ºB", "9ºC", "9ºD"]
TURMAS_TARDE = ["6ºA", "6ºB", "6ºC", "6ºD", "6ºE", "7ºA", "7ºB", "7ºC"]

def get_turno_by_turma(turma):
    if turma in TURMAS_MANHA:
        return "Manhã"
    elif turma in TURMAS_TARDE:
        return "Tarde"
    return "Manhã"

# ==================== FUNÇÕES OTIMIZADAS ====================
def get_todos_dados():
    """Busca TODOS os dados de uma vez e processa em memória"""
    cache_key = "todos_dados"
    cached = cache.get(cache_key)
    if cached:
        return cached
    
    hoje = datetime.now().strftime("%Y-%m-%d")
    
    materiais = supabase.table("materiais").select("*").execute().data
    emprestimos = supabase.table("emprestimos")\
        .select("material_id, turno, horario, quantidade_emprestada, data_emprestimo_data")\
        .is_("data_devolucao_real", "null")\
        .execute().data
    reservas = supabase.table("reservas")\
        .select("material_id, turno, horario, quantidade_reservada, data_retirada")\
        .execute().data
    
    uso = {}
    
    for e in emprestimos:
        if e.get("data_emprestimo_data") == hoje:
            key = f"{e['material_id']}_{e['turno']}_{e['horario']}"
            uso[key] = uso.get(key, 0) + e.get("quantidade_emprestada", 1)
    
    for r in reservas:
        if r.get("data_retirada") == hoje:
            key = f"{r['material_id']}_{r['turno']}_{r['horario']}"
            uso[key] = uso.get(key, 0) + r.get("quantidade_reservada", 1)
    
    for material in materiais:
        total = material["quantidade_total"]
        horarios_manha = []
        horarios_tarde = []
        total_disponivel_manha = 0
        total_disponivel_tarde = 0
        
        for horario in HORARIOS:
            usado_manha = uso.get(f"{material['id']}_Manhã_{horario}", 0)
            disp_manha = total - usado_manha
            if disp_manha > 0:
                horarios_manha.append(horario)
                total_disponivel_manha += disp_manha
            
            usado_tarde = uso.get(f"{material['id']}_Tarde_{horario}", 0)
            disp_tarde = total - usado_tarde
            if disp_tarde > 0:
                horarios_tarde.append(horario)
                total_disponivel_tarde += disp_tarde
        
        material["horarios_manha_hoje"] = horarios_manha
        material["horarios_tarde_hoje"] = horarios_tarde
        material["total"] = total
        material["disponiveis_manha"] = total_disponivel_manha
        material["disponiveis_tarde"] = total_disponivel_tarde
        material["disponiveis"] = total_disponivel_manha + total_disponivel_tarde
    
    total_materiais = sum(m["quantidade_total"] for m in materiais)
    total_emprestados = len(emprestimos)
    total_reservados = len(reservas)
    
    resultado = {
        'materiais': materiais,
        'total_materiais': total_materiais,
        'total_emprestados': total_emprestados,
        'total_reservados': total_reservados
    }
    
    cache.set(cache_key, resultado)
    return resultado

def get_disponibilidade_por_horario(material_id, data, turno, horario):
    cache_key = f"disp_{material_id}_{data}_{turno}_{horario}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    
    material = supabase.table("materiais").select("quantidade_total").eq("id", material_id).single().execute().data
    if not material:
        return 0
    
    total = material["quantidade_total"]
    
    emprestimos = supabase.table("emprestimos")\
        .select("quantidade_emprestada")\
        .eq("material_id", material_id)\
        .eq("data_emprestimo_data", data)\
        .eq("turno", turno)\
        .eq("horario", horario)\
        .is_("data_devolucao_real", "null")\
        .execute().data
    
    reservas = supabase.table("reservas")\
        .select("quantidade_reservada")\
        .eq("material_id", material_id)\
        .eq("data_retirada", data)\
        .eq("turno", turno)\
        .eq("horario", horario)\
        .execute().data
    
    total_emprestado = sum(e.get("quantidade_emprestada", 1) for e in emprestimos)
    total_reservado = sum(r.get("quantidade_reservada", 1) for r in reservas)
    disponivel = total - total_emprestado - total_reservado
    
    cache.set(cache_key, disponivel)
    return disponivel

# ==================== ROTAS DE AUTENTICAÇÃO ====================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip()
        senha = request.form["senha"].strip()
        
        usuario = supabase.table("usuarios")\
            .select("*")\
            .eq("email", email)\
            .execute().data
        
        if usuario and usuario[0]["senha"] == senha:
            session['usuario_id'] = usuario[0]["id"]
            session['usuario_nome'] = usuario[0]["nome"]
            session['usuario_email'] = usuario[0]["email"]
            session['role'] = usuario[0]["role"]
            flash(f"Bem-vindo, {usuario[0]['nome']}!", "success")
            return redirect(url_for("index"))
        else:
            flash("Email ou senha incorretos.", "error")
    
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    cache.clear()
    flash("Você saiu do sistema.", "success")
    return redirect(url_for("login"))

@app.route("/cadastrar_professor", methods=["GET", "POST"])
def cadastrar_professor():
    if request.method == "POST":
        email = request.form["email"].strip()
        senha = request.form["senha"].strip()
        nome = request.form["nome"].strip()
        confirmar_senha = request.form.get("confirmar_senha", "").strip()
        
        if not email or not senha or not nome:
            flash("Todos os campos são obrigatórios.", "error")
            return redirect(url_for("cadastrar_professor"))
        
        if senha != confirmar_senha:
            flash("As senhas não coincidem.", "error")
            return redirect(url_for("cadastrar_professor"))
        
        if len(senha) < 4:
            flash("A senha deve ter pelo menos 4 caracteres.", "error")
            return redirect(url_for("cadastrar_professor"))
        
        existente = supabase.table("usuarios").select("id").eq("email", email).execute().data
        if existente:
            flash("Este email já está cadastrado.", "error")
            return redirect(url_for("cadastrar_professor"))
        
        supabase.table("usuarios").insert({
            "email": email,
            "senha": senha,
            "nome": nome,
            "role": "professor",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }).execute()
        
        flash("Conta criada com sucesso! Faça login.", "success")
        return redirect(url_for("login"))
    
    return render_template("cadastrar_professor.html")

# ==================== ROTA PRINCIPAL ====================
@app.route("/")
@login_required
def index():
    try:
        dados = get_todos_dados()
        return render_template(
            "index.html",
            materiais=dados['materiais'],
            total_materiais=dados['total_materiais'],
            total_emprestados=dados['total_emprestados'],
            total_reservados=dados['total_reservados'],
            categorias=CATEGORIAS,
            usuario_nome=session.get('usuario_nome'),
            usuario_role=session.get('role')
        )
    except Exception as e:
        return f"Erro ao carregar dados: {str(e)}", 500

# ==================== CADASTRAR MATERIAL ====================
@app.route("/cadastrar", methods=["GET", "POST"])
@admin_required
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
            "data_aquisicao": datetime.now().strftime("%Y-%m-%d"),
            "created_by": session['usuario_id']
        }).execute()
        
        cache.clear()
        flash(f"Material '{nome}' cadastrado com sucesso!", "success")
        return redirect(url_for("index"))

    return render_template("cadastrar.html", categorias=CATEGORIAS)

# ==================== EMPRESTAR/RESERVAR ====================
@app.route("/emprestar/<int:material_id>", methods=["GET", "POST"])
@login_required
def emprestar(material_id):
    dados = get_todos_dados()
    material = next((m for m in dados['materiais'] if m['id'] == material_id), None)
    
    if not material:
        flash("Material não encontrado.", "error")
        return redirect(url_for("index"))

    if request.method == "POST":
        aluno = session['usuario_nome']
        turma = request.form["turma"]
        horario_index = int(request.form.get("horario", 0))
        horario = HORARIOS[horario_index] if horario_index < len(HORARIOS) else HORARIOS[0]
        quantidade = int(request.form.get("quantidade", 1))
        data_retirada = request.form.get("data_retirada", datetime.now().strftime("%Y-%m-%d"))
        turno = get_turno_by_turma(turma)
        
        disponivel = get_disponibilidade_por_horario(material_id, data_retirada, turno, horario)
        
        if quantidade > disponivel:
            flash(f"Apenas {disponivel} unidades disponíveis para {turno.lower()} no dia {data_retirada} no horário {horario}.", "error")
            return redirect(url_for("emprestar", material_id=material_id))
        
        if quantidade <= 0:
            flash("Quantidade inválida.", "error")
            return redirect(url_for("emprestar", material_id=material_id))
        
        data_devolucao_prevista = (datetime.strptime(data_retirada, "%Y-%m-%d") + timedelta(days=7)).strftime("%Y-%m-%d")
        
        if data_retirada == datetime.now().strftime("%Y-%m-%d"):
            supabase.table("emprestimos").insert({
                "material_id": material_id,
                "aluno": aluno,
                "turma": turma,
                "turno": turno,
                "horario": horario,
                "quantidade_emprestada": quantidade,
                "usuario_id": session['usuario_id'],
                "usuario_nome": session['usuario_nome'],
                "data_emprestimo": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "data_emprestimo_data": data_retirada,
                "data_devolucao_prevista": data_devolucao_prevista
            }).execute()
            flash(f"{quantidade}x '{material['nome']}' EMPRESTADO para {aluno} no horário {horario}!", "success")
        else:
            supabase.table("reservas").insert({
                "material_id": material_id,
                "aluno": aluno,
                "turma": turma,
                "turno": turno,
                "horario": horario,
                "quantidade_reservada": quantidade,
                "usuario_id": session['usuario_id'],
                "usuario_nome": session['usuario_nome'],
                "data_reserva": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "data_retirada": data_retirada
            }).execute()
            flash(f"{quantidade}x '{material['nome']}' RESERVADO para {aluno} no horário {horario} para o dia {data_retirada}!", "warning")

        cache.clear()
        return redirect(url_for("index"))
    
    hoje = datetime.now().strftime("%Y-%m-%d")
    disponibilidades = {}
    for horario in HORARIOS:
        for turno in ["Manhã", "Tarde"]:
            disponibilidades[f"{turno}_{horario}"] = get_disponibilidade_por_horario(material_id, hoje, turno, horario)
    
    return render_template("emprestar.html", 
                         material=material, 
                         turmas_manha=TURMAS_MANHA,
                         turmas_tarde=TURMAS_TARDE,
                         horarios=HORARIOS,
                         disponibilidades=disponibilidades,
                         hoje=hoje)

# ==================== ATUALIZAR RESERVA ====================
@app.route("/atualizar_reserva/<int:reserva_id>", methods=["GET", "POST"])
@login_required
def atualizar_reserva(reserva_id):
    reserva = supabase.table("reservas").select("*, materiais(*)").eq("id", reserva_id).single().execute().data
    if not reserva:
        flash("Reserva não encontrada.", "error")
        return redirect(url_for("reservas"))
    
    if not pode_editar_reserva(reserva_id, session['usuario_id'], session['role']):
        flash("Você só pode editar suas próprias reservas.", "error")
        return redirect(url_for("reservas"))

    if request.method == "POST":
        nova_data = request.form.get("data_retirada")
        novo_horario_index = int(request.form.get("horario", 0))
        novo_horario = HORARIOS[novo_horario_index] if novo_horario_index < len(HORARIOS) else HORARIOS[0]
        
        disponivel = get_disponibilidade_por_horario(
            reserva["material_id"], nova_data, reserva["turno"], novo_horario
        )
        
        if reserva["quantidade_reservada"] > disponivel:
            flash(f"Apenas {disponivel} unidades disponíveis para {reserva['turno'].lower()} no dia {nova_data} no horário {novo_horario}.", "error")
            return redirect(url_for("atualizar_reserva", reserva_id=reserva_id))
        
        supabase.table("reservas").update({
            "data_retirada": nova_data,
            "horario": novo_horario
        }).eq("id", reserva_id).execute()
        
        cache.clear()
        flash(f"Reserva atualizada para o dia {nova_data} no horário {novo_horario}!", "success")
        return redirect(url_for("reservas"))
    
    return render_template("atualizar_reserva.html", reserva=reserva, horarios=HORARIOS, hoje=datetime.now().strftime("%Y-%m-%d"))

# ==================== CANCELAR RESERVA ====================
@app.route("/cancelar_reserva/<int:reserva_id>", methods=["POST"])
@login_required
def cancelar_reserva(reserva_id):
    if not pode_editar_reserva(reserva_id, session['usuario_id'], session['role']):
        flash("Você só pode cancelar suas próprias reservas.", "error")
        return redirect(url_for("reservas"))
    
    try:
        supabase.table("reservas").delete().eq("id", reserva_id).execute()
        cache.clear()
        flash("Reserva cancelada com sucesso!", "success")
    except Exception as e:
        flash(f"Erro ao cancelar reserva: {str(e)}", "error")
    
    return redirect(url_for("reservas"))

# ==================== DEVOLVER MATERIAL ====================
@app.route("/devolver/<int:emprestimo_id>", methods=["POST"])
@login_required
def devolver(emprestimo_id):
    if not pode_devolver_emprestimo(emprestimo_id, session['usuario_id'], session['role']):
        flash("Você só pode devolver materiais que pegou emprestado.", "error")
        return redirect(url_for("emprestimos_ativos"))
    
    try:
        supabase.table("emprestimos")\
            .update({
                "data_devolucao_real": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            })\
            .eq("id", emprestimo_id)\
            .execute()
        
        cache.clear()
        flash("Material devolvido com sucesso!", "success")
    except Exception as e:
        flash(f"Erro na devolução: {str(e)}", "error")
    
    return redirect(url_for("emprestimos_ativos"))

# ==================== EXCLUIR MATERIAL ====================
@app.route("/excluir/<int:material_id>", methods=["POST"])
@admin_required
def excluir(material_id):
    supabase.table("materiais").delete().eq("id", material_id).execute()
    cache.clear()
    flash("Material excluído com sucesso!", "success")
    return redirect(url_for("index"))

# ==================== ATUALIZAR MATERIAL ====================
@app.route("/atualizar/<int:material_id>", methods=["GET", "POST"])
@admin_required
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
        cache.clear()
        flash(f"Material '{material['nome']}' atualizado com sucesso!", "success")
        return redirect(url_for("index"))

    return render_template("atualizar.html", material=material, categorias=CATEGORIAS)

# ==================== BUSCAR / AUTOCOMPLETE ====================
@app.route("/buscar")
@login_required
def buscar():
    termo = request.args.get("q", "").strip()
    if not termo:
        return redirect(url_for("index"))

    dados = get_todos_dados()
    materiais = [m for m in dados['materiais'] if termo.lower() in m['nome'].lower() or termo.lower() in m['categoria'].lower()]

    return render_template("index.html",
                           materiais=materiais,
                           termo=termo,
                           total_materiais=len(materiais),
                           total_emprestados=0,
                           categorias=CATEGORIAS)

@app.route("/autocomplete")
@login_required
def autocomplete():
    termo = request.args.get("q", "")
    if not termo:
        return jsonify([])

    dados = get_todos_dados()
    sugestoes = [m["nome"] for m in dados['materiais'] if termo.lower() in m['nome'].lower()][:10]
    return jsonify(sugestoes)

# ==================== LISTAR EMPRÉSTIMOS ====================
@app.route("/emprestimos_ativos")
@login_required
def emprestimos_ativos():
    dados = get_todos_dados()
    total_materiais = dados['total_materiais']
    
    emprestimos = supabase.table("emprestimos").select("*, materiais(*), usuarios!usuario_id(nome)")\
        .is_("data_devolucao_real", "null")\
        .order("data_emprestimo")\
        .execute().data
    
    for emp in emprestimos:
        if emp.get("usuarios"):
            emp["usuario_nome"] = emp["usuarios"]["nome"]
    
    total_emprestados = len(emprestimos)

    return render_template("emprestimos_ativos.html",
                           emprestimos=emprestimos,
                           total_materiais=total_materiais,
                           total_emprestados=total_emprestados)

# ==================== LISTAR RESERVAS ====================
@app.route("/reservas")
@login_required
def reservas():
    reservas_list = supabase.table("reservas").select("*, materiais(*), usuarios!usuario_id(nome)")\
        .order("data_retirada")\
        .execute().data
    
    for res in reservas_list:
        if res.get("usuarios"):
            res["usuario_nome"] = res["usuarios"]["nome"]
    
    total_reservas = len(reservas_list)
    return render_template("reservas.html", reservas=reservas_list, total_reservas=total_reservas)

# ==================== ADMIN - USUÁRIOS ====================
@app.route("/admin/usuarios")
@admin_required
def listar_usuarios():
    usuarios = supabase.table("usuarios").select("*").order("created_at").execute().data
    return render_template("admin_usuarios.html", usuarios=usuarios)

@app.route("/admin/usuarios/criar", methods=["GET", "POST"])
@admin_required
def criar_usuario():
    if request.method == "POST":
        email = request.form["email"].strip()
        senha = request.form["senha"].strip()
        nome = request.form["nome"].strip()
        role = request.form["role"]
        
        existente = supabase.table("usuarios").select("id").eq("email", email).execute().data
        if existente:
            flash("Este email já está cadastrado.", "error")
            return redirect(url_for("criar_usuario"))
        
        supabase.table("usuarios").insert({
            "email": email,
            "senha": senha,
            "nome": nome,
            "role": role,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }).execute()
        
        flash(f"Usuário {nome} criado com sucesso!", "success")
        return redirect(url_for("listar_usuarios"))
    
    return render_template("criar_usuario.html")

@app.route("/admin/usuarios/editar/<int:usuario_id>", methods=["GET", "POST"])
@admin_required
def editar_usuario(usuario_id):
    usuario = supabase.table("usuarios").select("*").eq("id", usuario_id).single().execute().data
    if not usuario:
        flash("Usuário não encontrado.", "error")
        return redirect(url_for("listar_usuarios"))
    
    if request.method == "POST":
        nome = request.form["nome"].strip()
        role = request.form["role"]
        senha = request.form.get("senha", "").strip()
        
        dados_update = {"nome": nome, "role": role}
        if senha:
            dados_update["senha"] = senha
        
        supabase.table("usuarios").update(dados_update).eq("id", usuario_id).execute()
        flash(f"Usuário {nome} atualizado com sucesso!", "success")
        return redirect(url_for("listar_usuarios"))
    
    return render_template("editar_usuario.html", usuario=usuario)

@app.route("/admin/usuarios/excluir/<int:usuario_id>", methods=["POST"])
@admin_required
def excluir_usuario(usuario_id):
    if usuario_id == session['usuario_id']:
        flash("Você não pode excluir seu próprio usuário.", "error")
        return redirect(url_for("listar_usuarios"))
    
    supabase.table("usuarios").delete().eq("id", usuario_id).execute()
    flash("Usuário excluído com sucesso!", "success")
    return redirect(url_for("listar_usuarios"))

@app.route("/admin/tornar_admin/<int:usuario_id>", methods=["POST"])
@admin_required
def tornar_admin(usuario_id):
    if usuario_id == session['usuario_id']:
        flash("Você já é administrador.", "warning")
        return redirect(url_for("listar_usuarios"))
    
    supabase.table("usuarios").update({"role": "admin"}).eq("id", usuario_id).execute()
    flash("Usuário promovido a administrador com sucesso!", "success")
    return redirect(url_for("listar_usuarios"))

@app.route("/admin/rebaixar_professor/<int:usuario_id>", methods=["POST"])
@admin_required
def rebaixar_professor(usuario_id):
    if usuario_id == session['usuario_id']:
        flash("Você não pode rebaixar a si mesmo.", "error")
        return redirect(url_for("listar_usuarios"))
    
    supabase.table("usuarios").update({"role": "professor"}).eq("id", usuario_id).execute()
    flash("Usuário rebaixado para professor.", "success")
    return redirect(url_for("listar_usuarios"))

# ==================== DASHBOARD ADMIN ====================
@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    dados = get_todos_dados()
    total_materiais = dados['total_materiais']
    total_emprestimos = dados['total_emprestados']
    total_reservas = dados['total_reservados']
    
    usuarios = supabase.table("usuarios").select("id").execute().data
    total_usuarios = len(usuarios)
    
    hoje = datetime.now()
    emprestimos_por_dia = []
    for i in range(7):
        data = (hoje - timedelta(days=i)).strftime("%Y-%m-%d")
        count = supabase.table("emprestimos").select("id").eq("data_emprestimo_data", data).execute().data
        emprestimos_por_dia.append({"data": data, "total": len(count)})
    
    top_materiais = supabase.table("emprestimos").select("materiais(nome), quantidade_emprestada").execute().data
    
    materiais_count = {}
    for e in top_materiais:
        nome = e.get("materiais", {}).get("nome", "Desconhecido")
        qtd = e.get("quantidade_emprestada", 1)
        materiais_count[nome] = materiais_count.get(nome, 0) + qtd
    
    top_5 = sorted(materiais_count.items(), key=lambda x: x[1], reverse=True)[:5]
    
    ultimos_emprestimos = supabase.table("emprestimos").select("*, materiais(nome), usuarios!usuario_id(nome)")\
        .order("data_emprestimo", desc=True).limit(10).execute().data
    
    for emp in ultimos_emprestimos:
        if emp.get("usuarios"):
            emp["usuario_nome"] = emp["usuarios"]["nome"]
    
    return render_template("admin_dashboard.html",
                         total_materiais=total_materiais,
                         total_emprestimos=total_emprestimos,
                         total_reservas=total_reservas,
                         total_usuarios=total_usuarios,
                         emprestimos_por_dia=emprestimos_por_dia,
                         top_materiais=top_5,
                         ultimos_emprestimos=ultimos_emprestimos)

# ==================== OCORRÊNCIAS ====================
# ==================== OCORRÊNCIAS ====================
TIPOS_OCORRENCIA = [
    {"id": "disciplina", "nome": "Questões Disciplinares/Comportamentais", "icone": "⚠️"},
    {"id": "agressao", "nome": "Agressão Escolar", "icone": "👊"},
    {"id": "dano", "nome": "Danos ao Patrimônio", "icone": "💔"},
    {"id": "bullying", "nome": "Bullying e Cyberbullying", "icone": "😔"},
    {"id": "aparelhos", "nome": "Uso Indevido de Aparelhos", "icone": "📱"},
    {"id": "infracional", "nome": "Ato Infracional/Segurança", "icone": "🚨"},
    {"id": "pedagogica", "nome": "Ocorrências Pedagógicas", "icone": "📚"},
    {"id": "outros", "nome": "Outros", "icone": "📝"}
]

@app.route("/ocorrencias")
@login_required
def listar_ocorrencias():
    """Lista ocorrências com filtros - professores veem as próprias, admin vê todas"""
    
    filtro_aluno = request.args.get("aluno", "").strip()
    filtro_turma = request.args.get("turma", "").strip()
    
    if session['role'] == 'admin':
        query = supabase.table("ocorrencias").select("*")
    else:
        query = supabase.table("ocorrencias").select("*").eq("usuario_id", session['usuario_id'])
    
    if filtro_aluno:
        query = query.ilike("nome_aluno", f"%{filtro_aluno}%")
    
    if filtro_turma:
        query = query.eq("turma", filtro_turma)
    
    ocorrencias = query.order("data_ocorrencia", desc=True).order("created_at", desc=True).execute().data
    
    notificacoes_pendentes = sum(1 for o in ocorrencias if o.get("notificar_pais"))
    
    tipos_map = {t["id"]: t for t in TIPOS_OCORRENCIA}
    
    return render_template("ocorrencias.html", 
                         ocorrencias=ocorrencias, 
                         filtro_aluno=filtro_aluno,
                         filtro_turma=filtro_turma,
                         turmas_manha=TURMAS_MANHA,
                         turmas_tarde=TURMAS_TARDE,
                         notificacoes_pendentes=notificacoes_pendentes,
                         tipos_map=tipos_map)

@app.route("/ocorrencias/nova", methods=["GET", "POST"])
@login_required
def nova_ocorrencia():
    if request.method == "POST":
        nome_aluno = request.form["nome_aluno"].strip()
        turma = request.form["turma"]
        tipo_ocorrencia = request.form.get("tipo_ocorrencia", "outros")
        descricao_personalizada = request.form.get("descricao_personalizada", "").strip()
        observacao = request.form.get("observacao", "").strip()
        notificar_pais = request.form.get("notificar_pais") == "on"
        
        if not nome_aluno:
            flash("Nome do aluno é obrigatório.", "error")
            return redirect(url_for("nova_ocorrencia"))
        
        # Se for "outros" e tiver descrição, usa a descrição
        if tipo_ocorrencia == "outros" and descricao_personalizada:
            ocorrencia_text = descricao_personalizada
        else:
            tipo_nome = next((t["nome"] for t in TIPOS_OCORRENCIA if t["id"] == tipo_ocorrencia), "Outros")
            ocorrencia_text = tipo_nome
        
        supabase.table("ocorrencias").insert({
            "usuario_id": session['usuario_id'],
            "usuario_nome": session['usuario_nome'],
            "data_ocorrencia": datetime.now().strftime("%Y-%m-%d"),
            "nome_aluno": nome_aluno,
            "turma": turma,
            "ocorrencia": ocorrencia_text,
            "tipo_ocorrencia": tipo_ocorrencia,
            "descricao_personalizada": descricao_personalizada if tipo_ocorrencia == "outros" else None,
            "observacao": observacao,
            "notificar_pais": notificar_pais,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "visualizada": False
        }).execute()
        
        cache.clear()
        flash("Ocorrência registrada com sucesso!", "success")
        return redirect(url_for("listar_ocorrencias"))
    
    return render_template("nova_ocorrencia.html", 
                         turmas_manha=TURMAS_MANHA, 
                         turmas_tarde=TURMAS_TARDE, 
                         datetime=datetime,
                         tipos_ocorrencia=TIPOS_OCORRENCIA)

@app.route("/ocorrencias/editar/<int:ocorrencia_id>", methods=["GET", "POST"])
@login_required
def editar_ocorrencia(ocorrencia_id):
    ocorrencia = supabase.table("ocorrencias").select("*").eq("id", ocorrencia_id).single().execute().data
    
    if not ocorrencia:
        flash("Ocorrência não encontrada.", "error")
        return redirect(url_for("listar_ocorrencias"))
    
    if session['role'] != 'admin' and ocorrencia['usuario_id'] != session['usuario_id']:
        flash("Você só pode editar suas próprias ocorrências.", "error")
        return redirect(url_for("listar_ocorrencias"))
    
    if request.method == "POST":
        nome_aluno = request.form["nome_aluno"].strip()
        turma = request.form["turma"]
        tipo_ocorrencia = request.form.get("tipo_ocorrencia", "outros")
        descricao_personalizada = request.form.get("descricao_personalizada", "").strip()
        observacao = request.form.get("observacao", "").strip()
        notificar_pais = request.form.get("notificar_pais") == "on"
        
        if not nome_aluno:
            flash("Nome do aluno é obrigatório.", "error")
            return redirect(url_for("editar_ocorrencia", ocorrencia_id=ocorrencia_id))
        
        if tipo_ocorrencia == "outros" and descricao_personalizada:
            ocorrencia_text = descricao_personalizada
        else:
            tipo_nome = next((t["nome"] for t in TIPOS_OCORRENCIA if t["id"] == tipo_ocorrencia), "Outros")
            ocorrencia_text = tipo_nome
        
        supabase.table("ocorrencias").update({
            "nome_aluno": nome_aluno,
            "turma": turma,
            "ocorrencia": ocorrencia_text,
            "tipo_ocorrencia": tipo_ocorrencia,
            "descricao_personalizada": descricao_personalizada if tipo_ocorrencia == "outros" else None,
            "observacao": observacao,
            "notificar_pais": notificar_pais
        }).eq("id", ocorrencia_id).execute()
        
        cache.clear()
        flash("Ocorrência atualizada com sucesso!", "success")
        return redirect(url_for("listar_ocorrencias"))
    
    return render_template("editar_ocorrencia.html", 
                         ocorrencia=ocorrencia, 
                         turmas_manha=TURMAS_MANHA, 
                         turmas_tarde=TURMAS_TARDE,
                         tipos_ocorrencia=TIPOS_OCORRENCIA)

@app.route("/ocorrencias/visualizar/<int:ocorrencia_id>")
@login_required
def visualizar_ocorrencia(ocorrencia_id):
    """Página expandida para visualizar a ocorrência completa"""
    ocorrencia = supabase.table("ocorrencias").select("*").eq("id", ocorrencia_id).single().execute().data
    
    if not ocorrencia:
        flash("Ocorrência não encontrada.", "error")
        return redirect(url_for("listar_ocorrencias"))
    
    if session['role'] != 'admin' and ocorrencia['usuario_id'] != session['usuario_id']:
        flash("Você não tem permissão para visualizar esta ocorrência.", "error")
        return redirect(url_for("listar_ocorrencias"))
    
    if session['role'] == 'admin' and not ocorrencia.get("visualizada"):
        supabase.table("ocorrencias").update({"visualizada": True}).eq("id", ocorrencia_id).execute()
    
    tipos_map = {t["id"]: t for t in TIPOS_OCORRENCIA}
    
    return render_template("visualizar_ocorrencia.html", 
                         ocorrencia=ocorrencia,
                         tipos_map=tipos_map)

@app.route("/ocorrencias/excluir/<int:ocorrencia_id>", methods=["POST"])
@login_required
def excluir_ocorrencia(ocorrencia_id):
    ocorrencia = supabase.table("ocorrencias").select("*").eq("id", ocorrencia_id).single().execute().data
    
    if not ocorrencia:
        flash("Ocorrência não encontrada.", "error")
        return redirect(url_for("listar_ocorrencias"))
    
    if session['role'] != 'admin' and ocorrencia['usuario_id'] != session['usuario_id']:
        flash("Você só pode excluir suas próprias ocorrências.", "error")
        return redirect(url_for("listar_ocorrencias"))
    
    supabase.table("ocorrencias").delete().eq("id", ocorrencia_id).execute()
    cache.clear()
    flash("Ocorrência excluída com sucesso!", "success")
    return redirect(url_for("listar_ocorrencias"))

@app.route("/ocorrencias/marcar_visualizada/<int:ocorrencia_id>", methods=["POST"])
@login_required
def marcar_visualizada(ocorrencia_id):
    if session['role'] != 'admin':
        return jsonify({"error": "Acesso negado"}), 403
    
    supabase.table("ocorrencias").update({"visualizada": True}).eq("id", ocorrencia_id).execute()
    return jsonify({"success": True})

@app.route("/ocorrencias/marcar_todas_visualizadas", methods=["POST"])
@login_required
def marcar_todas_visualizadas():
    if session['role'] != 'admin':
        return jsonify({"error": "Acesso negado"}), 403
    
    supabase.table("ocorrencias").update({"visualizada": True}).eq("visualizada", False).execute()
    return jsonify({"success": True})

# ==================== PROCESSAR RESERVAS ====================
@app.route("/processar_reservas")
@admin_required
def processar_reservas():
    try:
        hoje = datetime.now().strftime("%Y-%m-%d")
        reservas_hoje = supabase.table("reservas").select("*").eq("data_retirada", hoje).execute().data
        
        for reserva in reservas_hoje:
            disponivel = get_disponibilidade_por_horario(
                reserva["material_id"], hoje, reserva["turno"], reserva["horario"]
            )
            
            if disponivel >= reserva["quantidade_reservada"]:
                data_devolucao_prevista = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
                
                supabase.table("emprestimos").insert({
                    "material_id": reserva["material_id"],
                    "aluno": reserva["aluno"],
                    "turma": reserva["turma"],
                    "turno": reserva["turno"],
                    "horario": reserva["horario"],
                    "quantidade_emprestada": reserva["quantidade_reservada"],
                    "usuario_id": reserva["usuario_id"],
                    "usuario_nome": reserva.get("usuario_nome", ""),
                    "data_emprestimo": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "data_emprestimo_data": hoje,
                    "data_devolucao_prevista": data_devolucao_prevista
                }).execute()
                
                supabase.table("reservas").delete().eq("id", reserva["id"]).execute()
                flash(f"Reserva para {reserva['aluno']} processada!", "success")
            else:
                amanha = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
                supabase.table("reservas").update({"data_retirada": amanha}).eq("id", reserva["id"]).execute()
                flash(f"Reserva para {reserva['aluno']} adiada para amanhã (falta de material)", "warning")
        
        cache.clear()
    except Exception as e:
        flash(f"Erro ao processar reservas: {str(e)}", "error")
    
    return redirect(url_for("index"))

# ==================== CONTEXT PROCESSOR GLOBAL ====================
@app.context_processor
def inject_global_variables():
    """Injeta variáveis globais em todos os templates"""
    total_nao_visualizadas = 0
    if 'usuario_id' in session and session.get('role') == 'admin':
        try:
            nao_visualizadas = supabase.table("ocorrencias").select("id").eq("visualizada", False).execute().data
            total_nao_visualizadas = len(nao_visualizadas)
        except:
            total_nao_visualizadas = 0
    return dict(total_nao_visualizadas=total_nao_visualizadas)

if __name__ == "__main__":
    app.run(debug=True)


if __name__ == "__main__":
    app.run(debug=True)