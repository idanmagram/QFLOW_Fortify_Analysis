module top(clk, rst, state, key, out, Capacitance);

    input         clk;
    input         rst;
    input  [127:0] state;
    input  [127:0] key;
    output [127:0] out;
    output [63:0]  Capacitance;

    //wire [31:0] k0, v1, d;

    // AES core (named ports)
	
	
    aes_128 AES (
        .clk  (clk),
        .state(state),
        .key  (key),
        .out  (out)
    );
	
	
	
    // Trojan (optional)
     TSC tro (.rst (rst),.clk (clk),.key (key),.load(Capacitance));

endmodule


	

module aes_128(clk, state, key, out);
    input          clk;
    input  [127:0] state, key;
    output [127:0] out;

    reg  [127:0] s0, k0;

    wire [127:0] s1, s2, s3, s4, s5, s6, s7, s8, s9;
    wire [127:0] k1, k2, k3, k4, k5, k6, k7, k8, k9;
    wire [127:0] k0b, k1b, k2b, k3b, k4b, k5b, k6b, k7b, k8b, k9b;
    wire [127:0] k10_unused;
	assign k1 = key;
    always @(posedge clk) begin
        s0 <= state ^ key;
        k0 <= key;
    end

    // -------------------------------------------------------
    // KEY EXPANSION (10 rounds) — ONE LINE EACH
    // -------------------------------------------------------
	
    expand_key_128 a1  (.clk(clk), .in(k0),  .out_1(k1),  .out_2(k0b), .rcon(8'h01));
	
    expand_key_128 a2  (.clk(clk), .in(k1),  .out_1(k2),  .out_2(k1b), .rcon(8'h02));
    expand_key_128 a3  (.clk(clk), .in(k2),  .out_1(k3),  .out_2(k2b), .rcon(8'h04));
    expand_key_128 a4  (.clk(clk), .in(k3),  .out_1(k4),  .out_2(k3b), .rcon(8'h08));
    expand_key_128 a5  (.clk(clk), .in(k4),  .out_1(k5),  .out_2(k4b), .rcon(8'h10));
    expand_key_128 a6  (.clk(clk), .in(k5),  .out_1(k6),  .out_2(k5b), .rcon(8'h20));
    expand_key_128 a7  (.clk(clk), .in(k6),  .out_1(k7),  .out_2(k6b), .rcon(8'h40));
    expand_key_128 a8  (.clk(clk), .in(k7),  .out_1(k8),  .out_2(k7b), .rcon(8'h80));
    expand_key_128 a9  (.clk(clk), .in(k8),  .out_1(k9),  .out_2(k8b), .rcon(8'h1B));
    expand_key_128 a10 (.clk(clk), .in(k9),  .out_1(k10_unused), .out_2(k9b), .rcon(8'h36));
	

    // -------------------------------------------------------
    // ROUND FUNCTIONS (10 rounds, one per line)
    // -------------------------------------------------------
    one_round  r1 (.clk(clk), .state_in(s0), .key(k0b), .state_out(s1));
	
    one_round  r2 (.clk(clk), .state_in(s1), .key(k1b), .state_out(s2));
    one_round  r3 (.clk(clk), .state_in(s2), .key(k2b), .state_out(s3));
    one_round  r4 (.clk(clk), .state_in(s3), .key(k3b), .state_out(s4));
    one_round  r5 (.clk(clk), .state_in(s4), .key(k4b), .state_out(s5));
    one_round  r6 (.clk(clk), .state_in(s5), .key(k5b), .state_out(s6));
    one_round  r7 (.clk(clk), .state_in(s6), .key(k6b), .state_out(s7));
    one_round  r8 (.clk(clk), .state_in(s7), .key(k7b), .state_out(s8));
    one_round  r9 (.clk(clk), .state_in(s8), .key(k8b), .state_out(s9));
	

    final_round rf (.clk(clk), .state_in(s9), .key_in(k9b), .state_out(out));
	

endmodule



// It implements X^20 + X^13 + X^9 + X^5 + 1
module lfsr_counter (rst, clk, lfsr);
	input rst;
	input clk;
    output [19:0] lfsr;


	reg [19:0] lfsr_stream;
	wire d0; 
	
	
	assign lfsr = lfsr_stream; 
	//assign lfsr = 20'b10011001100110011001;

	// 4-input XOR built from binary XORs
	
	xor4_bit U_X4 (.o(d0),
                 .i1(lfsr_stream[15]),
                 .i2(lfsr_stream[11]),
                 .i3(lfsr_stream[7]),
                 .i4(lfsr_stream[0])); 

	always @(posedge clk)
		if (rst == 1) begin
			lfsr_stream <= 20'b10011001100110011001;
		end else begin
			lfsr_stream <= {d0,lfsr_stream[19:1]}; 
		end
	
		
endmodule

module one_round (clk, state_in, key, state_out);
    input              clk;
    input      [127:0] state_in, key;
    output reg [127:0] state_out;

    wire [31:0] s0, s1, s2, s3;
    wire [31:0] z0, z1, z2, z3;
    wire [31:0] k0, k1, k2, k3;

    assign k0 = key[127:96];
    assign k1 = key[95:64];
    assign k2 = key[63:32];
    assign k3 = key[31:0];

    assign s0 = state_in[127:96];
    assign s1 = state_in[95:64];
    assign s2 = state_in[63:32];
    assign s3 = state_in[31:0];

    wire [31:0] p00, p01, p02, p03,
                p10, p11, p12, p13,
                p20, p21, p22, p23,
                p30, p31, p32, p33;

    table_lookup
        t0 (.clk(clk), .state(s0), .p0(p00), .p1(p01), .p2(p02), .p3(p03)),
        t1 (.clk(clk), .state(s1), .p0(p10), .p1(p11), .p2(p12), .p3(p13)),
        t2 (.clk(clk), .state(s2), .p0(p20), .p1(p21), .p2(p22), .p3(p23)),
        t3 (.clk(clk), .state(s3), .p0(p30), .p1(p31), .p2(p32), .p3(p33));

	wire [31:0] z0_t0, z0_t1, z0_t2, z0;
	xor32 XZ0_0 (.o(z0_t0), .a(p00),    .b(p11));
	xor32 XZ0_1 (.o(z0_t1), .a(z0_t0),  .b(p22));
	xor32 XZ0_2 (.o(z0_t2), .a(z0_t1),  .b(p33));
	xor32 XZ0_3 (.o(z0),    .a(z0_t2),  .b(k0));

	// z1 = ((((p03 ^ p10) ^ p21) ^ p32) ^ k1)
	wire [31:0] z1_t0, z1_t1, z1_t2, z1;
	xor32 XZ1_0 (.o(z1_t0), .a(p03),    .b(p10));
	xor32 XZ1_1 (.o(z1_t1), .a(z1_t0),  .b(p21));
	xor32 XZ1_2 (.o(z1_t2), .a(z1_t1),  .b(p32));
	xor32 XZ1_3 (.o(z1),    .a(z1_t2),  .b(k1));

	// z2 = ((((p02 ^ p13) ^ p20) ^ p31) ^ k2)
	wire [31:0] z2_t0, z2_t1, z2_t2, z2;
	xor32 XZ2_0 (.o(z2_t0), .a(p02),    .b(p13));
	xor32 XZ2_1 (.o(z2_t1), .a(z2_t0),  .b(p20));
	xor32 XZ2_2 (.o(z2_t2), .a(z2_t1),  .b(p31));
	xor32 XZ2_3 (.o(z2),    .a(z2_t2),  .b(k2));

	// z3 = ((((p01 ^ p12) ^ p23) ^ p30) ^ k3)
	wire [31:0] z3_t0, z3_t1, z3_t2, z3;
	xor32 XZ3_0 (.o(z3_t0), .a(p01),    .b(p12));
	xor32 XZ3_1 (.o(z3_t1), .a(z3_t0),  .b(p23));
	xor32 XZ3_2 (.o(z3_t2), .a(z3_t1),  .b(p30));
	xor32 XZ3_3 (.o(z3),    .a(z3_t2),  .b(k3));


    always @ (posedge clk)
        state_out <= {z0, z1, z2, z3};
endmodule


/* AES final round for every two clock cycles */
module final_round (clk, state_in, key_in, state_out);
    input              clk;
    input      [127:0] state_in;
    input      [127:0] key_in;
    output reg [127:0] state_out;

    wire [31:0] s0, s1, s2, s3;
    wire [31:0] z0, z1, z2, z3;
    wire [31:0] k0, k1, k2, k3;

    // No LHS concat: explicit slices
    assign k0 = key_in[127:96];
    assign k1 = key_in[95:64];
    assign k2 = key_in[63:32];
    assign k3 = key_in[31:0];

    assign s0 = state_in[127:96];
    assign s1 = state_in[95:64];
    assign s2 = state_in[63:32];
    assign s3 = state_in[31:0];
 	
    wire [7:0] p00, p01, p02, p03;
	wire [7:0] p10, p11, p12, p13;
	wire [7:0] p20, p21, p22, p23;
	wire [7:0] p30, p31, p32, p33;

    wire [31:0] s4_1_w, s4_2_w, s4_3_w, s4_4_w;
    S4 S4_1 (.clk(clk), .in(s0), .out(s4_1_w));
    S4 S4_2 (.clk(clk), .in(s1), .out(s4_2_w));
    S4 S4_3 (.clk(clk), .in(s2), .out(s4_3_w));
    S4 S4_4 (.clk(clk), .in(s3), .out(s4_4_w));

	assign p00 = s4_1_w[31:24];
	assign p01 = s4_1_w[23:16];
	assign p02 = s4_1_w[15:8];
	assign p03 = s4_1_w[7:0];

	assign p10 = s4_2_w[31:24];
	assign p11 = s4_2_w[23:16];
	assign p12 = s4_2_w[15:8];
	assign p13 = s4_2_w[7:0];

	assign p20 = s4_3_w[31:24];
	assign p21 = s4_3_w[23:16];
	assign p22 = s4_3_w[15:8];
	assign p23 = s4_3_w[7:0];

	assign p30 = s4_4_w[31:24];
	assign p31 = s4_4_w[23:16];
	assign p32 = s4_4_w[15:8];
	assign p33 = s4_4_w[7:0];

    // intermediate concatenated words
    wire [31:0] z0_pre;
    wire [31:0] z1_pre;
    wire [31:0] z2_pre;
    wire [31:0] z3_pre;

    assign z0_pre = {p00, p11, p22, p33};
    assign z1_pre = {p10, p21, p32, p03};
    assign z2_pre = {p20, p31, p02, p13};
    assign z3_pre = {p30, p01, p12, p23};

    // 32-bit XORs done via gate-level xor32 modules
    xor32 XZ0 (.o(z0), .a(z0_pre), .b(k0));
    xor32 XZ1 (.o(z1), .a(z1_pre), .b(k1));
    xor32 XZ2 (.o(z2), .a(z2_pre), .b(k2));
    xor32 XZ3 (.o(z3), .a(z3_pre), .b(k3));

    always @ (posedge clk)
        state_out <= {z0, z1, z2, z3};
endmodule

module expand_key_128 (clk, in, out_1, out_2, rcon);
    input              clk;
    input      [127:0] in;
    input      [7:0]   rcon;
    output reg [127:0] out_1;
    output     [127:0] out_2;
    wire [31:0] k0, k1, k2, k3;
    wire [31:0] k3_rot;


    wire [31:0] v0, v1, v2, v3;
    reg  [31:0] k0a, k1a, k2a, k3a;
    wire [31:0] k0b, k1b, k2b, k3b;
    wire [31:0] k4a;

    
    assign k0 = in[127:96];
    assign k1 = in[95:64];
    assign k2 = in[63:32];
    assign k3 = in[31:0];
	
	//assign k3_rot = {k3[23:0], k3[31:24]};
	assign k3_rot[31:24] = k3[23:16];
	assign k3_rot[23:16] = k3[15:8];
	assign k3_rot[15:8]  = k3[7:0];
	assign k3_rot[7:0]   = k3[31:24];

	//assign v0 = { (k0[31:24] ^ rcon), k0[23:0] };
	//assign v0[31:24] = k0[31:24] ^ rcon; // top byte XOR rcon 
	xor8 X_v08 (.o(v0[31:24]), .a(k0[31:24]),.b(rcon));
	
	
	
	assign v0[23:16] = k0[23:16];
	assign v0[15:8]  = k0[15:8];
	assign v0[7:0]   = k0[7:0];

	// ---- v1 = v0 ^ k1; v2 = v1 ^ k2; v3 = v2 ^ k3 ----
	// Use your 32-bit XOR primitive so the DFG sees per-bit gates.
	xor32 X_v1 (.o(v1), .a(v0), .b(k1));
	xor32 X_v2 (.o(v2), .a(v1), .b(k2));
	xor32 X_v3 (.o(v3), .a(v2), .b(k3));

	always @(posedge clk) begin
		k0a <= v0;
		k1a <= v1;
		k2a <= v2;
		k3a <= v3;
	end

    S4 S4_0 (.clk(clk), .in(k3_rot), .out(k4a));


	xor32 X_k0b (.o(k0b), .a(k0a), .b(k4a));
	xor32 X_k1b (.o(k1b), .a(k1a), .b(k4a));
	xor32 X_k2b (.o(k2b), .a(k2a), .b(k4a));
	xor32 X_k3b (.o(k3b), .a(k3a), .b(k4a));
    //assign k0b = k0a ^ k4a;
    //assign k1b = k1a ^ k4a;
    //assign k2b = k2a ^ k4a;
    //assign k3b = k3a ^ k4a;

    always @ (posedge clk)
        out_1 <= {k0b, k1b, k2b, k3b};

    assign out_2 = {k0b, k1b, k2b, k3b};
endmodule



module table_lookup (clk, state, p0, p1, p2, p3);
  input         clk;
  input  [31:0] state;
  output [31:0] p0, p1, p2, p3;

  wire [7:0]  b0, b1, b2, b3;
  wire [31:0] t0_w, t1_w, t2_w;

  assign b0 = state[31:24];
  assign b1 = state[23:16];
  assign b2 = state[15:8];
  assign b3 = state[7:0];

  T t0 (.clk(clk), .in(b0), .out(t0_w));
  T t1 (.clk(clk), .in(b1), .out(t1_w));
  T t2 (.clk(clk), .in(b2), .out(t2_w));
  T t3 (.clk(clk), .in(b3), .out(p3));

      // p0 = ROL8(t0_w)
    assign p0 = { t0_w[23:0], t0_w[31:24] };

    // p1 = ROL16(t1_w)
    assign p1 = { t1_w[15:0], t1_w[31:16] };

    // p2 = ROL24(t2_w)
    assign p2 = { t2_w[7:0],  t2_w[31:8] };

    // p3 = ROL0 (identity)
    //assign p3 = t3_w;
endmodule


module S4 (clk, in, out);
  input clk;
  input  [31:0] in;
  output [31:0] out;

  wire [7:0] b0, b1, b2, b3;
  wire [7:0] o0, o1, o2, o3;

  assign b0 = in[31:24];
  assign b1 = in[23:16];
  assign b2 = in[15:8];
  assign b3 = in[7:0];

  S u0 (.clk(clk), .in(b0), .out(o0));
  S u1 (.clk(clk), .in(b1), .out(o1));
  S u2 (.clk(clk), .in(b2), .out(o2));
  S u3 (.clk(clk), .in(b3), .out(o3));

  assign out[31:24] = o0;
  assign out[23:16] = o1;
  assign out[15:8]  = o2;
  assign out[7:0]   = o3;
endmodule


/* S_box, S_box, S_box*(x+1), S_box*x */
module T (clk, in, out);
  input         clk;
  input  [7:0]  in;
  output [31:0] out;

  wire [7:0] s_byte, xs_byte;
  S  u_s  (.clk(clk), .in(in), .out(s_byte));
  xS u_xs (.clk(clk), .in(in), .out(xs_byte));

  assign out[31:24] = s_byte;
  assign out[23:16] = s_byte;
  assign out[15:8]  = (s_byte ^ xs_byte);
  assign out[7:0]   = xs_byte;
endmodule


// Simple pass-through S-box placeholder
module S (clk, in, out);
    input        clk;
    input  [7:0] in;
    output [7:0] out;

    // Combinational pass-through (or replace with 8'h00 if you prefer)
    assign out = in;
endmodule

// Simple pass-through inverse S-box placeholder
module xS (clk, in, out);
    input        clk;
    input  [7:0] in;
    output [7:0] out;

    // Combinational pass-through (placeholder for inverse S-box)
    assign out = in;
endmodule


module TSC(rst, clk, key, load);

    // ---- Ports ----
    input         rst;
    input         clk;
    input  [127:0] key;
    output [63:0]  load;

    // ---- Internals ----
		// load[0:0]/key[0] = 1 + 0.5 - 2*1*0.5 = 0.5
	// load[0:0]/key[1] = 0.5 + 0.5 - 2*0.5*0.5 = 0.5 
    reg  [63:0] load;
    wire [19:0] counter;

    lfsr_counter lfsr1 (.rst(rst), .clk(clk), .lfsr(counter));


    always @(posedge clk) begin
        load[0]  <= key[0] ^ counter[0];
        load[1]  <= key[0] ^ counter[0];
        load[2]  <= key[0] ^ counter[0];
        load[3]  <= key[0] ^ counter[0];
        load[4]  <= key[0] ^ counter[0];
        load[5]  <= key[0] ^ counter[0];
        load[6]  <= key[0] ^ counter[0];
        load[7]  <= key[0] ^ counter[0];

        load[8]  <= key[1] ^ counter[1];
        load[9]  <= key[1] ^ counter[1];
        load[10] <= key[1] ^ counter[1];
        load[11] <= key[1] ^ counter[1];
        load[12] <= key[1] ^ counter[1];
        load[13] <= key[1] ^ counter[1];
        load[14] <= key[1] ^ counter[1];
        load[15] <= key[1] ^ counter[1];

        load[16] <= key[2] ^ counter[2];
        load[17] <= key[2] ^ counter[2];
        load[18] <= key[2] ^ counter[2];
        load[19] <= key[2] ^ counter[2];
        load[20] <= key[2] ^ counter[2];
        load[21] <= key[2] ^ counter[2];
        load[22] <= key[2] ^ counter[2];
        load[23] <= key[2] ^ counter[2];

        load[24] <= key[3] ^ counter[3];
        load[25] <= key[3] ^ counter[3];
        load[26] <= key[3] ^ counter[3];
        load[27] <= key[3] ^ counter[3];
        load[28] <= key[3] ^ counter[3];
        load[29] <= key[3] ^ counter[3];
        load[30] <= key[3] ^ counter[3];
        load[31] <= key[3] ^ counter[3];

        load[32] <= key[4] ^ counter[4];
        load[33] <= key[4] ^ counter[4];
        load[34] <= key[4] ^ counter[4];
        load[35] <= key[4] ^ counter[4];
        load[36] <= key[4] ^ counter[4];
        load[37] <= key[4] ^ counter[4];
        load[38] <= key[4] ^ counter[4];
        load[39] <= key[4] ^ counter[4];

        load[40] <= key[5] ^ counter[5];
        load[41] <= key[5] ^ counter[5];
        load[42] <= key[5] ^ counter[5];
        load[43] <= key[5] ^ counter[5];
        load[44] <= key[5] ^ counter[5];
        load[45] <= key[5] ^ counter[5];
        load[46] <= key[5] ^ counter[5];
        load[47] <= key[5] ^ counter[5];

        load[48] <= key[6] ^ counter[6];
        load[49] <= key[6] ^ counter[6];
        load[50] <= key[6] ^ counter[6];
        load[51] <= key[6] ^ counter[6];
        load[52] <= key[6] ^ counter[6];
        load[53] <= key[6] ^ counter[6];
        load[54] <= key[6] ^ counter[6];
        load[55] <= key[6] ^ counter[6];

        load[56] <= key[7] ^ counter[7];
        load[57] <= key[7] ^ counter[7];
        load[58] <= key[7] ^ counter[7];
        load[59] <= key[7] ^ counter[7];
        load[60] <= key[7] ^ counter[7];
        load[61] <= key[7] ^ counter[7];
        load[62] <= key[7] ^ counter[7];
        load[63] <= key[7] ^ counter[7];
    end

endmodule


// Single-bit XORs (binary-only)
module xor2_bit(o, a, b);

    output o;
    input  a;
    input  b;

    assign o = a ^ b;

endmodule


module xor3_bit(o, a, b, c);

    output o;
    input  a;
    input  b;
    input  c;

    wire t;

    xor2_bit X1(.o(t), .a(a), .b(b));
    xor2_bit X2(.o(o), .a(t), .b(c));

endmodule


module xor4_bit(o, i1, i2, i3, i4);

    output o;
    input  i1;
    input  i2;
    input  i3;
    input  i4;

    wire t;

    xor3_bit X3(.o(t), .a(i1), .b(i2), .c(i3));
    xor2_bit X4(.o(o), .a(t),  .b(i4));

endmodule


// 32-bit bitwise XOR with explicit per-bit assigns (no generate)
module xor32(o, a, b);

    output [31:0] o;
    input  [31:0] a;
    input  [31:0] b;

    assign o[0]  = a[0]  ^ b[0];
    assign o[1]  = a[1]  ^ b[1];
    assign o[2]  = a[2]  ^ b[2];
    assign o[3]  = a[3]  ^ b[3];
    assign o[4]  = a[4]  ^ b[4];
    assign o[5]  = a[5]  ^ b[5];
    assign o[6]  = a[6]  ^ b[6];
    assign o[7]  = a[7]  ^ b[7];
    assign o[8]  = a[8]  ^ b[8];
    assign o[9]  = a[9]  ^ b[9];
    assign o[10] = a[10] ^ b[10];
    assign o[11] = a[11] ^ b[11];
    assign o[12] = a[12] ^ b[12];
    assign o[13] = a[13] ^ b[13];
    assign o[14] = a[14] ^ b[14];
    assign o[15] = a[15] ^ b[15];
    assign o[16] = a[16] ^ b[16];
    assign o[17] = a[17] ^ b[17];
    assign o[18] = a[18] ^ b[18];
    assign o[19] = a[19] ^ b[19];
    assign o[20] = a[20] ^ b[20];
    assign o[21] = a[21] ^ b[21];
    assign o[22] = a[22] ^ b[22];
    assign o[23] = a[23] ^ b[23];
    assign o[24] = a[24] ^ b[24];
    assign o[25] = a[25] ^ b[25];
    assign o[26] = a[26] ^ b[26];
    assign o[27] = a[27] ^ b[27];
    assign o[28] = a[28] ^ b[28];
    assign o[29] = a[29] ^ b[29];
    assign o[30] = a[30] ^ b[30];
    assign o[31] = a[31] ^ b[31];

endmodule


// 8-bit bitwise XOR with explicit per-bit assigns
module xor8(o, a, b);

    output [7:0] o;
    input  [7:0] a;
    input  [7:0] b;

    assign o[0] = a[0] ^ b[0];
    assign o[1] = a[1] ^ b[1];
    assign o[2] = a[2] ^ b[2];
    assign o[3] = a[3] ^ b[3];
    assign o[4] = a[4] ^ b[4];
    assign o[5] = a[5] ^ b[5];
    assign o[6] = a[6] ^ b[6];
    assign o[7] = a[7] ^ b[7];

endmodule

